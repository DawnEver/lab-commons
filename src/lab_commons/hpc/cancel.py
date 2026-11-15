"""``python -m lab_commons.hpc cancel (--at <user@host>|--grant <u>) (--job <id>...|--pending-over-mem <GB>)``.

THE ONE NARROW DOOR for cancelling Slurm jobs: a bare ``ssh`` to a login host is refused
(HPC-THROUGH-THE-DOOR), so a job that must go -- a large pending ask blocking a shared account, say -- needs
an auditable verb, not a shell.

- ONE ``squeue -u <user>`` read lists that user's jobs (id, state, memory, owner, name); a row whose owner is
  not the user is dropped again, so a foreign job is never selected.
- ``--job`` selects by id (an array's base id selects its rows); ``--pending-over-mem`` selects only PENDING
  jobs whose memory ask (Slurm's ``%m``) exceeds that many GiB. A running or small job is never selected by
  size. No selector is refused.
- Every selected job is listed (``id state mem name``); ``--dry-run`` stops there. Otherwise ONE
  ``scancel -u <user> <ids>`` cancels exactly those ids. Each use is logged: who, where, what -- never a secret.

Exit 2: usage or refusal (nothing cancelled). Exit 1: a ``--job`` id matched no own job (the rest still go).
Remote commands share the host's one gated connection, each bounded by ``--timeout``.
"""

from __future__ import annotations

import argparse
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from lab_commons.hpc.cluster import Runner, runner_for, ssh_runner
from lab_commons.hpc.grants import AccessRefused, Grant, Machine, check_target, load_grants
from lab_commons.log import emit, log

__all__ = ['FORMAT', 'JOB_ID', 'TIMEOUT_S', 'Job', 'gigabytes', 'main', 'parse', 'select']

#: The ``squeue`` columns read: id|state|min memory|user|name (name last: it may hold the separator).
FORMAT: Final = '%i|%T|%m|%u|%j'

#: The separators in one ``squeue`` row of :data:`FORMAT`.
_SEPARATORS: Final = FORMAT.count('|')

#: A job id as given on the CLI: a number, optionally one array task.
JOB_ID: Final = re.compile(r'^\d+(?:_\d+)?$')

#: Seconds each remote command may take: a queue read and one scancel are quick.
TIMEOUT_S: Final = 60.0

_UNITS: Final = {'K': 1 / 1024**2, 'M': 1 / 1024, 'G': 1.0, 'T': 1024.0}

type Reach = Callable[[Grant, float], Runner]


@dataclass(frozen=True)
class Job:
    """One ``squeue`` row."""

    id: str
    state: str
    mem: str
    user: str
    name: str


def gigabytes(mem: str) -> float:
    """Slurm's memory text (``284G``, ``4000M``; a bare number is MiB) in GiB."""
    unit = mem[-1:].upper()
    if unit in _UNITS:
        return float(mem[:-1]) * _UNITS[unit]
    return float(mem) * _UNITS['M']


def parse(squeue: str, user: str) -> list[Job]:
    """The rows owned by *user*; any other owner is dropped."""
    jobs = [Job(*line.split('|', _SEPARATORS)) for line in squeue.splitlines() if line.count('|') >= _SEPARATORS]
    return [j for j in jobs if j.user == user]


def select(jobs: list[Job], ids: list[str], over_gb: float | None) -> list[Job]:
    """The jobs named by id (or array base id), plus every PENDING one asking more than *over_gb* GiB."""
    wanted = set(ids)

    def chosen(job: Job) -> bool:
        if job.id in wanted or job.id.partition('_')[0] in wanted:
            return True
        return over_gb is not None and job.state == 'PENDING' and gigabytes(job.mem) > over_gb

    return [j for j in jobs if chosen(j)]


class _Refused(Exception):
    """Nothing is cancelled; the message names why (exit 2)."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc cancel', description=__doc__.splitlines()[0])
    parser.add_argument('--at', help='ssh target user@host; reaches it directly, no machine file needed')
    parser.add_argument('--grant', help='the grant user to act as; needed only when the machine holds several')
    parser.add_argument('--job', action='append', default=[], help='a job id to cancel (repeatable)')
    parser.add_argument('--pending-over-mem', type=float, metavar='GB', help='cancel PENDING jobs asking > GB GiB')
    parser.add_argument('--dry-run', action='store_true', help='list what would be cancelled; cancel nothing')
    parser.add_argument('--timeout', type=float, default=TIMEOUT_S, help=f'seconds per remote command ({TIMEOUT_S:g})')
    return parser


def _grant(machine: Machine, user: str | None) -> Grant:
    if user is None and len(machine.grants) == 1:
        return machine.grants[0]
    if user is None:
        msg = f'this machine holds several grants; pass --grant <user> ({", ".join(g.user for g in machine.grants)})'
        raise _Refused(msg)
    try:
        return machine.grant(user)
    except (AccessRefused, LookupError) as refused:
        raise _Refused(str(refused)) from None


def _validate(args: argparse.Namespace) -> None:
    if not args.job and args.pending_over_mem is None:
        msg = 'name what to cancel: --job <id>... or --pending-over-mem <GB>'
        raise _Refused(msg)
    if bad := [j for j in args.job if not JOB_ID.fullmatch(j)]:
        msg = f'not a job id: {", ".join(map(repr, bad))}'
        raise _Refused(msg)


def _reach(
    args: argparse.Namespace, machine: Machine | None, reach: Reach, at: Callable[[str, float], Runner]
) -> tuple[Runner, str, str]:
    """The runner, the acting user and where it acts."""
    if args.at:
        user, sep, _host = args.at.partition('@')
        if not sep or not user:
            msg = f'--at must be user@host, got {args.at!r}'
            raise _Refused(msg)
        check_target(args.at)
        return at(args.at, args.timeout), user, args.at
    grant = _grant(machine or load_grants(), args.grant)
    return reach(grant, args.timeout), grant.user, grant.name


def main(
    argv: list[str],
    *,
    machine: Machine | None = None,
    reach: Reach = runner_for,
    at: Callable[[str, float], Runner] = ssh_runner,
) -> int:
    """List the selected own jobs, then cancel them unless ``--dry-run``; see the module docstring for exits."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        _validate(args)
        run, user, where = _reach(args, machine, reach, at)
    except (_Refused, AccessRefused) as refused:
        emit(f'cancel refused: {refused}', err=True)
        return 2
    jobs = parse(run(f'squeue -u {shlex.quote(user)} -h -o {shlex.quote(FORMAT)}', None), user)
    chosen = select(jobs, args.job, args.pending_over_mem)
    found = {j.id for j in chosen} | {j.id.partition('_')[0] for j in chosen}
    missing = [j for j in args.job if j not in found]
    for job_id in missing:
        emit(f'no own job {job_id} in the queue of {user} on {where}', err=True)
    verb = 'would cancel' if args.dry_run else 'cancel'
    for job in chosen:
        emit(f'{verb} {job.id} {job.state} {job.mem} {job.name}')
    ids = [j.id for j in chosen]
    log(f'hpc cancel by {user} at {where}: {"dry-run " if args.dry_run else ""}{" ".join(ids) or "nothing"}')
    if ids and not args.dry_run:
        run(f'scancel -u {shlex.quote(user)} {" ".join(map(shlex.quote, ids))}', None)
    return 1 if missing else 0
