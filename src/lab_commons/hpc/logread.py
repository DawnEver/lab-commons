"""``python -m lab_commons.hpc log --at <user@host> [--lines N] <path>``: read a run's log on a cluster account.

User ruling 2026-11-11: every agent publishes, GATHERS and cleans Ada work through the key-only doors; a forge
status naming ``log=<path>`` had no door that could read it. This is that door, and it is READ-ONLY and CONFINED:

- *path* is relative to the account root (``$LAB_ROOT``) or absolute under it; ``..`` and anything else are
  refused before any ssh. The remote side resolves the real path and refuses one outside the account root (a symlink).
- at most :data:`MAX_LINES` lines, the file's tail; one ssh session through the gated runner
  (:func:`lab_commons.hpc.cluster.ssh_runner`: shared connection, rate-limited).

Exit 0 printed, 2 refused, 3 the host did not answer (one line, no traceback; try again later).
"""

from __future__ import annotations

import argparse
import posixpath
import shlex
from collections.abc import Callable
from typing import Final

from lab_commons.hpc.cluster import Runner, Unreachable, ssh_runner
from lab_commons.hpc.grants import AccessRefused, check_target
from lab_commons.hpc.layout import REQUIRE_ROOT, ROOT
from lab_commons.log import emit

__all__ = ['MAX_LINES', 'OUTSIDE', 'main']

#: The most lines one read prints: a log's tail, never a bulk copy.
MAX_LINES: Final = 400
#: What the remote side prints for a path that resolves outside the account root.
OUTSIDE: Final = 'LOG-OUTSIDE-ROOT'
_TIMEOUT_S: Final = 60.0


class _Refused(ValueError):
    pass


def _lines(lines: int) -> int:
    if not 1 <= lines <= MAX_LINES:
        msg = f'--lines must be 1..{MAX_LINES}'
        raise _Refused(msg)
    return lines


def _relative(path: str) -> str:
    """*path* checked locally: no ``..``; the remote side refuses anything resolving outside the account root."""
    if not path or '..' in path.split('/'):
        msg = f'{path!r} must name a file under the account root with no ".."'
        raise _Refused(msg)
    return posixpath.normpath(path)


def _command(relative: str, lines: int) -> str:
    quoted = shlex.quote(relative)
    return (
        f'{REQUIRE_ROOT} && r=$(realpath -e -- {ROOT}) && cd "$r" && p=$(realpath -e -- {quoted}) && '
        f'case "$p" in "$r"/*) tail -n {lines} -- "$p";; '
        f'*) echo {OUTSIDE};; esac'
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc log', description=__doc__.splitlines()[0])
    parser.add_argument('--at', required=True, help='ssh target user@host')
    parser.add_argument('--lines', type=int, default=120, help=f'tail lines, 1..{MAX_LINES}')
    parser.add_argument('path', help='a file under the account root: relative to it, or absolute')
    return parser


def main(argv: list[str], *, at: Callable[[str, float], Runner] = ssh_runner) -> int:
    """Validate, then print the file's tail; see the module docstring for exits."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        lines, relative = _lines(args.lines), _relative(args.path)
    except _Refused as refused:
        emit(f'log refused: {refused}', err=True)
        return 2
    try:
        check_target(args.at)
    except AccessRefused as refused:
        emit(f'log refused: {refused}', err=True)
        return 2
    try:
        said = at(args.at, _TIMEOUT_S)(_command(relative, lines), None)
    except Unreachable as down:
        emit(f'log: host unreachable -- {down}; nothing was read, try again later', err=True)
        return 3
    if said.strip() == OUTSIDE:
        emit(f'log refused: {args.path!r} resolves outside the account root', err=True)
        return 2
    emit(said.rstrip('\n'))
    return 0
