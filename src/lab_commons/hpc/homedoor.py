"""THE HOME DOOR: the quota guard every tree-making verb passes, the tick's home pass, and their CLI.

``python -m lab_commons.hpc tree acquire <repo-url> <sha> --purpose <why> [--ttl <hours>] [--holder <name>]``
leases a commit's tree under ``~/ci/leases`` (:mod:`lab_commons.hpc.lease`) and prints its path;
``tree release <sha>``, ``tree list`` and ``tree reap`` complete it. ``python -m lab_commons.hpc home`` prints the
quota, the reaped leases and every stray directory -- exactly what the forge tick runs (:func:`home_pass`).

THE GUARD (:func:`guard`), before ``tree acquire`` and ``run submit``: the quota is read
(:data:`lab_commons.hpc.home.QUOTA_SCRIPT`); at :data:`~lab_commons.hpc.home.QUOTA` ``soft`` the expired leases are
reaped FIRST and the quota re-read, then a warning names the usage; at ``hard`` the verb is REFUSED by name,
``HOME-QUOTA-HARD``, with the usage and the home's top consumers. An unreadable quota is warned and never read
as zero.

WHERE IT RUNS. On a login node (``sbatch`` on PATH) it uses that node's shell; elsewhere it reaches the cluster over
``ssh`` -- ``--at user@host`` names the target directly and needs no machine file, else ``--grant <user>`` picks a
grant of the machine file's ``[hpc]`` table (:mod:`lab_commons.hpc.grants`).
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from lab_commons.hpc.cluster import Runner, local_runner, runner_for, ssh_runner
from lab_commons.hpc.grants import SESSION_ENV, AccessRefused, check_target, load_grants
from lab_commons.hpc.home import (
    QUOTA,
    QUOTA_SCRIPT,
    Consumer,
    Usage,
    census_script,
    parse_census,
    parse_usage,
    stray_text,
    strays,
)
from lab_commons.hpc.lease import MAX_TTL_HOURS, LeaseRefused, Reaped, acquire, held, reap, release
from lab_commons.hpc.shell import UV_CACHE
from lab_commons.log import emit

__all__ = ['HomeReport', 'QuotaRefused', 'env_hint', 'guard', 'home_main', 'home_pass', 'reach', 'tree_main']

#: Default lease length: a working day and a night; renew by acquiring again.
_TTL_HOURS = 24.0

type Say = Callable[[str], object]


class QuotaRefused(RuntimeError):
    """``HOME-QUOTA-HARD``: the home is past the hard threshold; nothing new was made."""


def _warn(text: str) -> None:
    emit(text, err=True)


def read_usage(run: Runner) -> Usage | None:
    """The home quota as the cluster reports it; ``None`` when nothing answered."""
    return parse_usage(run(QUOTA_SCRIPT, None))


def census(run: Runner) -> list[Consumer]:
    """Every top-level home directory and its (capped) file count, most first."""
    return parse_census(run(census_script(), None))


def _top(consumers: Iterable[Consumer], n: int = 6) -> str:
    return '; '.join(c.text() for c in list(consumers)[:n]) or 'none counted'


def guard(run: Runner, act: str, *, now: float, say: Say = _warn) -> Usage | None:
    """Reap at ``soft``, refuse *act* at ``hard`` (:class:`QuotaRefused`); the usage it judged, ``None`` unread."""
    usage = read_usage(run)
    if usage is None:
        say(f'HOME-QUOTA: the home quota is unreadable; {act} proceeds unguarded')
        return None
    if usage.fraction() >= QUOTA.soft:
        gone = [r for r in reap(run, now=now) if r.gone]
        usage = read_usage(run) or usage
        say(
            f'HOME-QUOTA-SOFT: the home is at {usage.fraction():.0%} of its {usage.binding()} quota '
            f'(>= {QUOTA.soft:.0%}): {usage.text()}; reaped {len(gone)} expired leased tree(s) first. '
            'Release leases you are done with: python -m lab_commons.hpc tree release <sha>'
        )
    if usage.fraction() >= QUOTA.hard:
        msg = (
            f'HOME-QUOTA-HARD: {act} refused -- the home is at {usage.fraction():.0%} of its {usage.binding()} quota '
            f'(>= {QUOTA.hard:.0%}): {usage.text()}. Top consumers: {_top(census(run))}. Remedy: release leases '
            '(python -m lab_commons.hpc tree release <sha>), remove a stray through '
            'python -m lab_commons.hpc clean --root <home> <name>, then retry'
        )
        raise QuotaRefused(msg)
    return usage


@dataclass(frozen=True)
class HomeReport:
    """One home pass: the usage, the trees reaped (or kept), and the stray directories found."""

    usage: Usage | None
    reaped: list[Reaped] = field(default_factory=list)
    strays: list[Consumer] = field(default_factory=list)

    @property
    def level(self) -> str:
        """``ok``, ``soft``, ``hard`` or ``unread`` -- the quota threshold the home is past."""
        if self.usage is None:
            return 'unread'
        f = self.usage.fraction()
        return 'hard' if f >= QUOTA.hard else 'soft' if f >= QUOTA.soft else 'ok'

    def lines(self) -> list[str]:
        """What the tick logs: the usage and level, each reap, each stray with its remedy."""
        quota = self.usage.text() if self.usage else 'unreadable'
        out = [f'[home] quota {quota}: {self.level}']
        out += [f'[home] {r.text()}' for r in self.reaped]
        out += [f'[home] {line}' for line in stray_text(self.strays)]
        return out


def home_pass(run: Runner, *, now: float, managed: Iterable[str] = ()) -> HomeReport:
    """THE TICK'S HOME PASS: reap expired leases, read the quota, name every stray past the managed roots.

    *managed* adds owned roots the caller knows (a deploy's checkout root). Nothing but expired leased trees is
    ever removed; a stray is named, never touched.
    """
    reaped = reap(run, now=now)
    usage = read_usage(run)
    return HomeReport(usage, reaped, strays(census(run), managed))


def reach(at: str | None, grant: str | None) -> tuple[Runner, str]:
    """The runner and its name: ``--at user@host`` over ssh, else this login node, else a machine-file grant."""
    try:
        if at:
            check_target(at)
            return ssh_runner(at), at
        if shutil.which('sbatch') is not None and grant is None:
            return local_runner, socket.gethostname()
        machine = load_grants()
        chosen = [machine.grant(grant)] if grant else list(machine.grants)
    except (AccessRefused, LookupError) as refused:
        raise LeaseRefused(str(refused)) from None
    if len(chosen) != 1:
        msg = f'pass --at user@host, or --grant <user> naming one of: {", ".join(g.user for g in machine.grants)}'
        raise LeaseRefused(msg)
    return runner_for(chosen[0]), chosen[0].name


def env_hint(tree: str) -> str:
    """How to build inside a leased *tree*: its env and build dirs live IN it, so the reap takes them too.

    uv HARDLINKS from the one shared cache (``~/ci/uv-cache``, the run builds' own), so an env costs its directories,
    not a copy of every file -- per-tree caches were a large part of ``~/ab``'s 2.3 M files.
    """
    return (
        f'build inside the lease, never beside it: UV_CACHE_DIR={UV_CACHE} UV_LINK_MODE=hardlink uv venv {tree}/.venv; '
        f'cargo builds use --target-dir {tree}/target'
    )


def _holder() -> str:
    return os.environ.get(SESSION_ENV, '').strip() or f'{getpass.getuser()}@{socket.gethostname().split(".")[0]}'


def _reach_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--at', help='ssh target user@host; needs no machine file')
    parser.add_argument('--grant', help='the [hpc] grant user to reach through (when --at is not given)')


def _tree_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc tree', description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest='verb', required=True)
    get = verbs.add_parser('acquire', help='lease a commit tree under ~/ci/leases; prints its path')
    get.add_argument('repo_url')
    get.add_argument('sha')
    get.add_argument('--purpose', required=True, help='why: one line, e.g. "A/B of the mesh refine"')
    get.add_argument('--ttl', type=float, default=_TTL_HOURS, help=f'hours, at most {MAX_TTL_HOURS}')
    get.add_argument('--holder', default=None, help=f'default ${SESSION_ENV}, else user@host')
    drop = verbs.add_parser('release', help="drop this holder's lease; the tree goes at the next reap")
    drop.add_argument('sha')
    drop.add_argument('--holder', default=None)
    verbs.add_parser('list', help='every lease, live or expired')
    verbs.add_parser('reap', help='remove the trees no live lease or job holds')
    for sub in (get, drop, *(verbs.choices[v] for v in ('list', 'reap'))):
        _reach_args(sub)
    return parser


def tree_main(argv: list[str], *, connect: Callable[[str | None, str | None], tuple[Runner, str]] = reach) -> int:
    """``tree acquire|release|list|reap``; exit 2 on a refusal (nothing made), 1 when a reap left a tree."""
    try:
        args = _tree_parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    now = time.time()
    try:
        run, where = connect(args.at, args.grant)
        if args.verb == 'acquire':
            guard(run, f'tree acquire {args.sha[:12]} on {where}', now=now)
            tree = acquire(
                run,
                args.repo_url,
                args.sha,
                holder=args.holder or _holder(),
                purpose=args.purpose,
                ttl_hours=args.ttl,
                now=now,
            )
            emit(tree)
            _warn(env_hint(tree))
        elif args.verb == 'release':
            release(run, args.sha, args.holder or _holder())
        elif args.verb == 'list':
            for lease in held(run):
                emit(lease.text(now))
        else:
            done = reap(run, now=now)
            for one in done:
                emit(one.text())
            return 1 if any(one.outcome == 'left' for one in done) else 0
    except (LeaseRefused, QuotaRefused, ValueError) as refused:
        emit(f'tree {args.verb} refused: {refused}', err=True)
        return 2
    return 0


def home_main(argv: list[str], *, connect: Callable[[str | None, str | None], tuple[Runner, str]] = reach) -> int:
    """``home``: one home pass, printed; exit 1 at ``hard`` or with a stray, else 0."""
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc home', description='the home pass the tick runs')
    _reach_args(parser)
    parser.add_argument('--managed', action='append', default=[], help='another owned top-level home directory')
    try:
        args = parser.parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        run, _ = connect(args.at, args.grant)
    except (LeaseRefused, ValueError) as refused:
        emit(f'home refused: {refused}', err=True)
        return 2
    report = home_pass(run, now=time.time(), managed=args.managed)
    for line in report.lines():
        emit(line)
    return 1 if report.level == 'hard' or report.strays else 0
