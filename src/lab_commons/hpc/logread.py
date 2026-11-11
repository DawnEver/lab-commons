"""``python -m lab_commons.hpc log --at <user@host> [--lines N] <path> | --tick <repo>``: read a cluster log.

User ruling 2026-11-11: every agent publishes, GATHERS and cleans Ada work through the key-only doors; a forge
status naming ``log=<path>`` had no door that could read it. This is that door, and it is READ-ONLY and CONFINED:

- *path* is relative to ``~/ci`` or an absolute path with a ``/ci/`` component; ``..`` and anything else are
  refused before any ssh. The remote side resolves the real path and refuses one outside ``$HOME/ci/`` (a symlink).
- at most :data:`MAX_LINES` lines, the file's tail; one ssh session through the gated runner
  (:func:`lab_commons.hpc.cluster.ssh_runner`: shared connection, rate-limited).

Exit 0 printed, 2 refused, 3 the host did not answer (one line, no traceback; try again later).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shlex
from collections.abc import Callable
from typing import Final

from lab_commons.hpc.cluster import Runner, Unreachable, ssh_runner
from lab_commons.log import emit

__all__ = ['MAX_LINES', 'OUTSIDE', 'main']

#: The most lines one read prints: a log's tail, never a bulk copy.
MAX_LINES: Final = 400
#: What the remote side prints for a path that resolves outside ``~/ci``.
OUTSIDE: Final = 'LOG-OUTSIDE-CI'
_TIMEOUT_S: Final = 60.0
#: A repo name as the forge tick names its log ``~/.cache/lab-forge-<repo>.log``.
_REPO: Final = re.compile(r'[a-z0-9][a-z0-9-]*')


class _Refused(ValueError):
    pass


def _lines(lines: int) -> int:
    if not 1 <= lines <= MAX_LINES:
        msg = f'--lines must be 1..{MAX_LINES}'
        raise _Refused(msg)
    return lines


def _relative(path: str) -> str:
    """*path* as a path under ``~/ci``; :class:`_Refused` for anything that could leave it."""
    if path.startswith('/'):
        head, sep, rest = path.partition('/ci/')
        if not sep or not head:
            msg = f'{path!r} is not under an account ci root (<home>/ci/...)'
            raise _Refused(msg)
        path = rest
    parts = path.split('/')
    if not path or '..' in parts or path.startswith('/'):
        msg = f'{path!r} must name a file under ~/ci with no ".."'
        raise _Refused(msg)
    return posixpath.normpath(path)


def _command(relative: str, lines: int) -> str:
    quoted = shlex.quote(relative)
    return (
        f'cd "$HOME/ci" && p=$(realpath -e -- {quoted}) && case "$p" in "$HOME/ci/"*) tail -n {lines} -- "$p";; '
        f'*) echo {OUTSIDE};; esac'
    )


def _tick_command(repo: str, lines: int) -> str:
    if not _REPO.fullmatch(repo):
        msg = f'--tick {repo!r} is not a repo name ([a-z0-9-])'
        raise _Refused(msg)
    return f'tail -n {lines} -- "$HOME/.cache/lab-forge-{repo}.log"'


def _chosen(tick: str | None, path: str | None, lines: int) -> str:
    """The one remote command: a repo's tick log or a file under ``~/ci`` -- exactly one of them."""
    if (tick is None) == (path is None):
        msg = 'name exactly one of --tick <repo> or a path under ~/ci'
        raise _Refused(msg)
    return _tick_command(tick, lines) if tick is not None else _command(_relative(path), lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc log', description=__doc__.splitlines()[0])
    parser.add_argument('--at', required=True, help='ssh target user@host')
    parser.add_argument('--lines', type=int, default=120, help=f'tail lines, 1..{MAX_LINES}')
    parser.add_argument('--tick', help="a repo's forge tick log, ~/.cache/lab-forge-<repo>.log (instead of a path)")
    parser.add_argument('path', nargs='?', help='a file under ~/ci: relative, or absolute with a /ci/ component')
    return parser


def main(argv: list[str], *, at: Callable[[str, float], Runner] = ssh_runner) -> int:
    """Validate, then print the file's tail; see the module docstring for exits."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        command = _chosen(args.tick, args.path, _lines(args.lines))
    except _Refused as refused:
        emit(f'log refused: {refused}', err=True)
        return 2
    try:
        said = at(args.at, _TIMEOUT_S)(command, None)
    except Unreachable as down:
        emit(f'log: host unreachable -- {down}; nothing was read, try again later', err=True)
        return 3
    if said.strip() == OUTSIDE:
        emit(f'log refused: {args.path!r} resolves outside ~/ci', err=True)
        return 2
    emit(said.rstrip('\n'))
    return 0
