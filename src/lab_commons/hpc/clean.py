"""``python -m lab_commons.hpc clean --root <abs> [--dry-run] [--timeout s] [--at <user@host>|--grant <u>] <name>...``.

THE ONE NARROW DOOR for removing finished output on a cluster: an ad-hoc ``rm -rf`` on a shared path is
refused by an agent's permission classifier unless the exact target is named, and a categorical authority
("old data under this root may go") needs an auditable door, not an allowlist row for ``rm``.

- ``--root`` is a REMOTE absolute path on the grant's login host, given on the CLI only (no machine-file
  or per-repo declaration: the caller's Makefile names it, one source). It IS the confinement; there is no
  further prefix allowlist.
- Each NAME is one plain path component (:data:`NAME`); the target is refused unless its resolved parent
  equals the resolved root.
- A RUNNING Slurm job (``squeue -t R %Z``, read through the same grant) whose work directory sits under any
  target refuses the WHOLE invocation before anything is deleted.
- After ``rm -rf`` each target is verified absent: the remote command's LAST line is its verdict, ``gone`` or
  ``present``, with what remains listed above it by owner (a ``.lab-hpc`` record dir written by another account
  cannot be removed by this one -- report only, no escalation). A delete whose command failed or answered no
  verdict is judged by re-reading the target's existence: the delete is idempotent, so an exit status is never
  the verdict (2026-11-25: a clean of 18 directories deleted every one, its verification step failed, and the
  door exited 1 with no account of what it had done).
- EVERY RUN ENDS IN ONE SUMMARY LINE: ``clean: deleted <n> of <m> under <root>: <names>`` (exit 0), else the
  same count and ``NOT removed: <name> (<why>)`` for each target left (exit 1).

``--at user@host`` reaches the login host directly over ``ssh`` and needs no machine file; without it the grant
comes from the ``[hpc]`` table (:mod:`lab_commons.hpc.grants`).

A deletion has no short ceiling: ``--timeout`` (default :data:`TIMEOUT_S`) bounds each remote command, because
``rm -rf`` of 14 GB on GPFS outran a fixed 300 s (2026-11-11). A command that still outruns it is reported, not
raised: the delete is idempotent, so re-running the same invocation finishes it.

Exit 2: usage, validation or a refusal (nothing deleted). Exit 1: any target left behind, each with its why.
Runs over ``ssh`` through the grant like ``status``/``gather``; it never touches the workstation.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable
from typing import Final

from lab_commons.hpc.cluster import Runner, Unreachable, runner_for, ssh_runner
from lab_commons.hpc.grants import AccessRefused, Grant, Machine, check_target, load_grants
from lab_commons.log import emit

__all__ = ['DELETE', 'MARK', 'NAME', 'PROBE', 'TIMEOUT_S', 'contained', 'main', 'running_under', 'valid_name']

#: One plain path component: no ``/``, no ``..``, no glob, no whitespace, no leading dot or dash.
NAME: Final = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

#: Seconds each remote command may take unless ``--timeout`` says otherwise: a large GPFS delete takes minutes.
TIMEOUT_S: Final = 3600

#: Remote command heads, ``<head><MARK><quoted target>``, so a fake runner reads which step was asked.
MARK: Final = ' #target '
PROBE: Final = 'r=$(realpath -e -- "$ROOT" 2>/dev/null); t=$(realpath -m -- "$T")'
DELETE: Final = 'rm -rf -- "$T" 2>/dev/null'

_PROBE_TAIL: Final = (
    '; if [ -e "$T" ]; then e=1; s=$(du -sh -- "$T" 2>/dev/null | cut -f1); else e=0; s=; fi'
    '; printf "%s\\t%s\\t%s\\t%s\\n" "$r" "$t" "$e" "$s"'
)
#: The verdict is the LAST line and the command exits 0 either way; what another account owns is listed above it.
_DELETE_TAIL: Final = (
    '; if [ -e "$T" ]; then find "$T" ! -user "$(id -un)" -prune -printf "%u\\t%p\\n" 2>/dev/null; echo present;'
    ' else echo gone; fi'
)

_SQUEUE: Final = "squeue -t R -h -o '%Z'"

type Reach = Callable[[Grant, float], Runner]


def valid_name(name: str) -> bool:
    """Whether *name* is one plain path component below the root."""
    return NAME.fullmatch(name) is not None


def contained(root_real: str, target_real: str) -> bool:
    """Whether the resolved target sits DIRECTLY under the resolved root."""
    return posixpath.dirname(target_real.rstrip('/')) == root_real.rstrip('/')


def running_under(squeue: str, targets: Iterable[str]) -> dict[str, list[str]]:
    """Each target with the running work directories at or below it (path prefix, not substring)."""
    dirs = [line.strip().rstrip('/') for line in squeue.splitlines() if line.strip()]
    found = {t: [d for d in dirs if d == t or d.startswith(t + '/')] for t in targets}
    return {t: ds for t, ds in found.items() if ds}


def _remote(head: str, tail: str, root: str, target: str) -> str:
    return f'ROOT={shlex.quote(root)}; T={shlex.quote(target)}; {head}{tail}{MARK}{shlex.quote(target)}'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc clean', description=__doc__.splitlines()[0])
    parser.add_argument('--root', required=True, help='REMOTE absolute directory the names sit directly under')
    parser.add_argument('--dry-run', action='store_true', help='print each target and its size; delete nothing')
    parser.add_argument(
        '--timeout', type=float, default=TIMEOUT_S, help=f'seconds per remote command (default {TIMEOUT_S})'
    )
    parser.add_argument('--at', help='ssh target user@host; reaches it directly, no machine file needed')
    parser.add_argument('--grant', help='the grant user to act as; needed only when the machine holds several')
    parser.add_argument('names', nargs='+', help='plain directory names directly under --root')
    return parser


class _Refused(Exception):
    """Nothing is deleted; the message names why (exit 2)."""


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


def _validate(root: str, names: list[str]) -> None:
    if not posixpath.isabs(root) or '..' in root.split('/'):
        msg = f'--root must be an absolute remote path without "..", got {root!r}'
        raise _Refused(msg)
    if bad := [n for n in names if not valid_name(n)]:
        msg = f'not a single plain path component: {", ".join(map(repr, bad))}'
        raise _Refused(msg)


def _probe(run: Runner, root: str, target: str) -> tuple[str, str, bool, str]:
    """The resolved root and target, whether the target exists, and its size.

    Raises:
        ValueError: the answer is not the probe's four tab-separated fields.

    """
    answer = run(_remote(PROBE, _PROBE_TAIL, root, target), None).rstrip('\n')
    fields = answer.split('\t')
    if len(fields) != 4:  # noqa: PLR2004 -- the probe's printf prints four fields
        msg = f'the probe of {target} answered {answer!r}, not root, target, exists and size'
        raise ValueError(msg)
    root_real, target_real, exists, size = fields
    return root_real, target_real, exists == '1', size


def _resolve(run: Runner, root: str, names: list[str], where: str) -> list[tuple[str, bool, str]]:
    """Each name's resolved target, whether it exists and its size; refused unless directly under the root."""
    targets = []
    for name in dict.fromkeys(names):
        try:
            root_real, target_real, exists, size = _probe(run, root, posixpath.join(root, name))
        except Unreachable:
            raise  # the hpc boundary names its door
        except (RuntimeError, ValueError) as unread:
            msg = f'{name} could not be probed on {where}: {unread}'
            raise _Refused(msg) from None
        if not root_real:
            msg = f'--root {root} does not exist on {where}'
            raise _Refused(msg)
        if not contained(root_real, target_real):
            msg = f'{name} resolves to {target_real}, not directly under {root_real}'
            raise _Refused(msg)
        targets.append((target_real, exists, size))
    live = running_under(run(_SQUEUE, None), [t for t, _, _ in targets])
    if live:
        msg = '; '.join(f'a RUNNING job works under {t}: {", ".join(ds)}' for t, ds in live.items())
        raise _Refused(msg)
    return targets


def _delete(run: Runner, root: str, target: str) -> str | None:
    """Delete *target* and verify it: ``None`` when it is gone, else why it is not."""
    try:
        lines = run(_remote(DELETE, _DELETE_TAIL, root, target), None).strip().splitlines()
    except RuntimeError as failed:  # an ssh exit or an unreachable host: the target's existence decides
        lines, answer = [], f'the delete command failed: {failed}'
    else:
        answer = f'the delete command answered {lines[-1] if lines else "nothing"!r}, not a verdict'
    if lines and lines[-1] == 'gone':
        emit(f'gone: {target}', flush=True)
        return None
    if lines and lines[-1] == 'present':
        owned = '\n'.join(lines[:-1])
        why = 'left behind (owner, path):\n' + owned if owned else 'still exists after rm -rf; re-run the same command'
        emit(f'NOT REMOVED: {target}; {why}', err=True, flush=True)
        return why.splitlines()[0]
    try:
        exists = _probe(run, root, target)[2]
    except (RuntimeError, ValueError) as unread:
        why = f'{answer}; its existence could not be re-read: {unread}'
        emit(f'NOT VERIFIED: {target}; {why}', err=True, flush=True)
        return why
    if not exists:
        emit(f'gone: {target} (verified absent; {answer})', flush=True)
        return None
    emit(f'NOT REMOVED: {target}; {answer}', err=True, flush=True)
    return answer


def main(
    argv: list[str],
    *,
    machine: Machine | None = None,
    reach: Reach = runner_for,
    at: Callable[[str, float], Runner] = ssh_runner,
) -> int:
    """Validate, guard, then delete and verify each named directory; see the module docstring for exits."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        _validate(args.root, args.names)
        if args.at:
            check_target(args.at)
            run, where = at(args.at, args.timeout), args.at
        else:
            grant = _grant(machine or load_grants(), args.grant)
            run, where = reach(grant, args.timeout), grant.name
        targets = _resolve(run, args.root, args.names, where)
    except (_Refused, AccessRefused) as refused:
        emit(f'clean refused: {refused}', err=True)
        return 2
    deleted: list[str] = []
    left: dict[str, str] = {}
    for target, exists, size in targets:
        if not exists:
            emit(f'absent: {target}', flush=True)
        elif args.dry_run:
            emit(f'would delete {target} ({size or "unknown"})', flush=True)
        else:
            emit(f'delete {target} ({size or "unknown"})', flush=True)
            try:
                why = _delete(run, args.root, target)
            except subprocess.TimeoutExpired:
                why = f'timed out after {args.timeout:g}s'
                emit(
                    f'TIMED OUT after {args.timeout:g}s deleting {target}; the delete is idempotent: re-run the same '
                    'command to finish it (raise --timeout for a large tree)',
                    err=True,
                    flush=True,
                )
            if why is None:
                deleted.append(target)
            else:
                left[target] = why
    if args.dry_run:
        emit(f'clean: dry run, would delete {sum(exists for _, exists, _ in targets)} under {args.root}', flush=True)
        return 0
    return _summary(args.root, deleted, left)


def _summary(root: str, deleted: list[str], left: dict[str, str]) -> int:
    """The one closing line: what was deleted, and each target left with its why; exit 0 only when none is left."""
    names = ', '.join(posixpath.basename(t) for t in deleted) or 'nothing'
    count = f'clean: deleted {len(deleted)} of {len(deleted) + len(left)} under {root}'
    if not left:
        emit(f'{count}: {names}', flush=True)
        return 0
    missed = '; '.join(f'{posixpath.basename(t)} ({why})' for t, why in left.items())
    emit(f'{count}; NOT removed: {missed}; deleted: {names}', err=True, flush=True)
    return 1
