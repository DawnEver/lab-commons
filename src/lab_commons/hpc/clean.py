"""``python -m lab_commons.hpc clean --root <abs> [--dry-run] [--grant <user>] <name>...`` -- delete named dirs.

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
- After ``rm -rf`` each target is verified absent; what remains is reported with its owner (a ``.lab-hpc``
  record dir written by another account cannot be removed by this one -- report only, no escalation).

Exit 2: usage, validation or a refusal (nothing deleted). Exit 1: any target left behind. Runs over ``ssh``
through the grant like ``status``/``gather``; it never touches the workstation.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shlex
from collections.abc import Callable, Iterable
from typing import Final

from lab_commons.hpc.cluster import Runner, runner_for
from lab_commons.hpc.grants import Grant, Machine, load_grants
from lab_commons.log import emit

__all__ = ['DELETE', 'MARK', 'NAME', 'PROBE', 'contained', 'main', 'running_under', 'valid_name']

#: One plain path component: no ``/``, no ``..``, no glob, no whitespace, no leading dot or dash.
NAME: Final = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

#: Remote command heads, ``<head><MARK><quoted target>``, so a fake runner reads which step was asked.
MARK: Final = ' #target '
PROBE: Final = 'r=$(realpath -e -- "$ROOT" 2>/dev/null); t=$(realpath -m -- "$T")'
DELETE: Final = 'rm -rf -- "$T" 2>/dev/null'

_PROBE_TAIL: Final = (
    '; if [ -e "$T" ]; then e=1; s=$(du -sh -- "$T" 2>/dev/null | cut -f1); else e=0; s=; fi'
    '; printf "%s\\t%s\\t%s\\t%s\\n" "$r" "$t" "$e" "$s"'
)
_DELETE_TAIL: Final = (
    '; if [ ! -e "$T" ]; then echo gone; else'
    ' f=$(find "$T" ! -user "$(id -un)" -prune -printf "%u\\t%p\\n" 2>/dev/null);'
    ' if [ -n "$f" ]; then printf "%s\\n" "$f"; else stat -c "%U\\t%n" -- "$T"; fi; fi'
)

_SQUEUE: Final = "squeue -t R -h -o '%Z'"

type Reach = Callable[[Grant], Runner]


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
    for grant in machine.grants:
        if grant.user == user:
            return grant
    msg = f'no grant for user {user!r}'
    raise _Refused(msg)


def _validate(root: str, names: list[str]) -> None:
    if not posixpath.isabs(root) or '..' in root.split('/'):
        msg = f'--root must be an absolute remote path without "..", got {root!r}'
        raise _Refused(msg)
    if bad := [n for n in names if not valid_name(n)]:
        msg = f'not a single plain path component: {", ".join(map(repr, bad))}'
        raise _Refused(msg)


def _resolve(run: Runner, root: str, names: list[str], where: str) -> list[tuple[str, bool, str]]:
    """Each name's resolved target, whether it exists and its size; refused unless directly under the root."""
    targets = []
    for name in dict.fromkeys(names):
        line = run(_remote(PROBE, _PROBE_TAIL, root, posixpath.join(root, name)), None).rstrip('\n')
        root_real, target_real, exists, size = line.split('\t')
        if not root_real:
            msg = f'--root {root} does not exist on {where}'
            raise _Refused(msg)
        if not contained(root_real, target_real):
            msg = f'{name} resolves to {target_real}, not directly under {root_real}'
            raise _Refused(msg)
        targets.append((target_real, exists == '1', size))
    live = running_under(run(_SQUEUE, None), [t for t, _, _ in targets])
    if live:
        msg = '; '.join(f'a RUNNING job works under {t}: {", ".join(ds)}' for t, ds in live.items())
        raise _Refused(msg)
    return targets


def _delete(run: Runner, root: str, target: str) -> int:
    left = run(_remote(DELETE, _DELETE_TAIL, root, target), None).strip()
    if left == 'gone':
        emit(f'gone: {target}')
        return 0
    emit(f'NOT REMOVED: {target}; left behind (owner, path):\n{left}', err=True)
    return 1


def main(argv: list[str], *, machine: Machine | None = None, reach: Reach = runner_for) -> int:
    """Validate, guard, then delete and verify each named directory; see the module docstring for exits."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        _validate(args.root, args.names)
        grant = _grant(machine or load_grants(), args.grant)
        run = reach(grant)
        targets = _resolve(run, args.root, args.names, grant.name)
    except _Refused as refused:
        emit(f'clean refused: {refused}', err=True)
        return 2
    residual = 0
    for target, exists, size in targets:
        if not exists:
            emit(f'absent: {target}')
        elif args.dry_run:
            emit(f'would delete {target} ({size or "unknown"})')
        else:
            emit(f'delete {target} ({size or "unknown"})')
            residual |= _delete(run, args.root, target)
    return residual
