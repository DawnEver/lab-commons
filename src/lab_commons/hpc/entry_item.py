"""The item runner of a part that declares its own ENTRY (``RunSpec.entry``): items are not pytest ids.

Shipped as text to ``<root>/serve/bin/lab_ci_entry_item.py`` beside the pytest item runner, whose stream
writer and reader it imports by that shipped name; stdlib-only otherwise, like it.

COLLECTION (in the prep job, cwd the tree): ``python lab_ci_entry_item.py <module:items> <sha>`` prints one
``@@@ item <json>`` line per item ``items(tree, sha)`` returns; each item names its ``node``.

A RUN (:func:`run`, the array's entry): each node's payload goes to the declared entry, which returns
``{'exit': code, ...}``. Exit 0 is ``ok``, any other ``rejected`` naming the code; an entry that RAISED could
not run the item at all -- ``error`` of kind ``environment``, its reason the exception.
"""

from __future__ import annotations

import json
import pkgutil
import sys
import time
from pathlib import Path
from typing import Any, Final

from lab_ci_pytest_item import _write, read_stream, split_streams

__all__ = ['ITEM_LINE', 'main', 'run']

#: The prefix of one collected item's line.
ITEM_LINE: Final = '@@@ item '


def _cell(node: str, said: object) -> dict[str, Any]:
    code = said.get('exit') if isinstance(said, dict) else None
    if code == 0:
        return {'status': 'ok'}
    return {'status': 'rejected', 'reason': {'kind': 'exit', 'text': f'{node}: exit {code}'}}


def run(item: dict[str, Any]) -> dict[str, Any]:
    """Run each of ``item['ids']`` through ``item['entry']`` on its payload, streaming one cell per id."""
    function = pkgutil.resolve_name(item['entry'])
    stream, index = Path(item['stream']), int(item['item'])
    stream.parent.mkdir(parents=True, exist_ok=True)
    target = str(stream.resolve())
    _write(target, index, {'begin': time.time()})
    start = time.monotonic()
    for node in item['ids']:
        _write(target, index, {'start': node})
        began = time.monotonic()
        try:
            cell = _cell(node, function(item['payloads'][node]))
        except Exception as raised:  # noqa: BLE001 -- the item could not run: its cause is the cell
            cell = {'status': 'error', 'reason': {'kind': 'environment', 'text': f'{type(raised).__name__}: {raised}'}}
        _write(target, index, {'id': node, 's': round(time.monotonic() - began, 3), **cell})
    _write(target, index, {'done': 0, 'wall': round(time.monotonic() - start, 3), 'peak_mb': None})
    mine = split_streams(stream.read_text(encoding='utf-8'), '').get(index, '')
    return read_stream(mine, list(item['ids']))


def main(argv: list[str]) -> int:
    """Print the items of ``<module:items> <sha>`` over the tree in the cwd, one :data:`ITEM_LINE` each."""
    items, sha = argv
    for one in pkgutil.resolve_name(items)(Path.cwd(), sha):
        print(ITEM_LINE + json.dumps(one))  # noqa: T201 -- the prep step's output is this
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
