"""``python -m lab_commons.hpc fetch -c <job> --item N --path <glob> [-o DIR] [--budget-mb M]``: an item's files.

``gather`` returns what an item RETURNED; what the item WROTE stays in its ``out`` directory on the cluster, and a
result or diagnosis on disk there must never cost a re-run to read (issue: a 4 MB event log and the result rows
were only listed as omitted). This door reads them, READ-ONLY and CONFINED:

- the item is the job file's item ``N``; its ``out`` directory is the item's own ``out``; the grant is the one the
  recorded submission placed that item on (``<job>.submission.json``) -- nothing is named by hand;
- *glob* is relative to ``out`` (a ``find -path`` pattern, ``*`` crosses directories); ``..`` or a leading ``/`` is
  refused before any ssh, and the remote side refuses a file whose real path leaves ``out``;
- every match is LISTED with its size; files are copied whole, smallest first, until ``--budget-mb`` is spent, and
  each one left is named with its size and why -- never dropped silently. Files land under ``-o`` (default
  ``<job>.fetched/<N>/`` beside the job file) at their path relative to ``out``.

Exit 0 copied (some may be named as over budget), 2 refused, 3 the host did not answer.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.cluster import Runner, Unreachable, runner_for
from lab_commons.hpc.config import load_config
from lab_commons.hpc.grants import Grant, load_grants
from lab_commons.hpc.jobs import grant_of
from lab_commons.log import emit

__all__ = ['OUTSIDE', 'main']

#: What the remote side prints for a match whose real path leaves the item's ``out``.
OUTSIDE: Final = 'FETCH-OUTSIDE-OUT'
_DEFAULT_BUDGET_MB: Final = 64.0
_MB: Final = 1024 * 1024


class _Refused(ValueError):
    pass


def _pattern(glob: str) -> str:
    if not glob or glob.startswith('/') or '..' in glob.split('/'):
        msg = f'--path {glob!r} must be relative to the item out directory, with no ".."'
        raise _Refused(msg)
    return glob


def _placed(record: dict[str, Any], index: int) -> dict[str, Any]:
    for placement in record['placements']:
        start, stop = placement['items']
        if start <= index < stop:
            return placement
    msg = f'item {index} is in no placement of the recorded submission'
    raise _Refused(msg)


def _list(out: str, glob: str) -> str:
    root = shlex.quote(out)
    return (
        f'cd {root} && R=$(pwd -P) && find . -type f -path {shlex.quote("./" + glob)} -print0 | '
        f'while IFS= read -r -d "" f; do p=$(realpath -e -- "$f"); case "$p" in "$R"/*) '
        f'printf "%s\t%s\n" "$(stat -c %s -- "$f")" "${{f#./}}";; *) printf "{OUTSIDE}\t%s\n" "$f";; esac; done'
    )


def _read(out: str, relative: str) -> str:
    return f'cd {shlex.quote(out)} && base64 -w0 -- {shlex.quote(relative)}'


def fetch(run: Runner, out: str, glob: str, dest: Path, budget: int) -> list[str]:
    """Copy *out*'s files matching *glob* into *dest*, smallest first within *budget* bytes; one line per file."""
    listed = [line.split('\t', 1) for line in run(_list(out, _pattern(glob)), None).splitlines() if '\t' in line]
    said = [f'refused {name}: it resolves outside {out}' for size, name in listed if size == OUTSIDE]
    files = sorted((int(size), name) for size, name in listed if size != OUTSIDE)
    if not files and not said:
        return [f'no file under {out} matches {glob!r}']
    spent = 0
    for size, name in files:
        if spent + size > budget:
            said.append(f'over budget {name} ({size} bytes): raise --budget-mb or narrow --path')
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(run(_read(out, name), None).strip()))
        spent += size
        said.append(f'fetched {name} ({size} bytes) -> {target}')
    return said


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc fetch', description=__doc__.splitlines()[0])
    parser.add_argument('-c', '--config', type=Path, required=True, help='the job file submit used')
    parser.add_argument('--item', type=int, required=True, help='the item index in the job file items')
    parser.add_argument('--path', required=True, help='a glob relative to the item out directory')
    parser.add_argument('-o', '--output', type=Path, help='where files land (default <job>.fetched/<item>/)')
    parser.add_argument('--budget-mb', type=float, default=_DEFAULT_BUDGET_MB, help='bytes copied at most, in MB')
    return parser


def _out_of(items: object, index: int, where: Path) -> str:
    """Item ``index``'s ``out`` directory; refused when the index or the item does not name one."""
    item = items[index] if isinstance(items, list) and 0 <= index < len(items) else None
    if not isinstance(item, dict) or 'out' not in item:
        msg = f'item {index} is not an item with an "out" directory in {where}'
        raise _Refused(msg)
    return str(item['out'])


def main(argv: list[str], *, reach: Callable[[Grant], Runner] = runner_for) -> int:
    """Resolve the item's out and grant from the job file and its submission, then copy; see the module docstring."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    try:
        config = load_config(args.config)
        out = _out_of(json.loads(config.items_path().read_text(encoding='utf-8')), args.item, config.items_path())
        record = json.loads(args.config.with_suffix('.submission.json').read_text(encoding='utf-8'))
        grant: Grant = grant_of(_placed(record, args.item), load_grants())
        dest = args.output or args.config.with_suffix('.fetched') / str(args.item)
        lines = fetch(reach(grant), out, args.path, dest, int(args.budget_mb * _MB))
    except (_Refused, OSError, ValueError) as refused:
        emit(f'fetch refused: {refused}', err=True)
        return 2
    except Unreachable as down:
        emit(f'fetch: host unreachable -- {down}; nothing more was read, try again later', err=True)
        return 3
    for line in lines:
        emit(line)
    return 0
