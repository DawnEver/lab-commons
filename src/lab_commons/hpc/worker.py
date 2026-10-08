"""What one array task runs on a compute node: its shard of the manifest, one item at a time.

``python -m lab_commons.hpc.worker <manifest.json> <shard index>``

AN ITEM'S FAILURE IS DATA, A SHARD'S IS SLURM'S. An exception raised by the entry point is recorded
against that item and the shard carries on, so one bad design does not cost its neighbours; the
results file is written atomically at the end, so a shard killed by Slurm (time, memory) leaves NO file
and shows as failed in ``sacct`` -- never as a half-written success.
"""

from __future__ import annotations

import json
import pkgutil
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lab_commons.log import emit

__all__ = ['load_entry', 'main', 'run_shard']

#: The positional arguments of an array task: the manifest path and the shard index.
_ARGS = ('manifest', 'shard')


def load_entry(spec: str) -> Callable[[Any], Any]:
    """``package.module:function`` to the callable it names."""
    module, sep, name = spec.partition(':')
    if not sep or not module or not name:
        msg = f'an entry point is "module:function", got {spec!r}'
        raise ValueError(msg)
    return pkgutil.resolve_name(spec)


def run_shard(manifest: dict[str, Any], shard: int, entry: Callable[[Any], Any] | None = None) -> dict[str, Any]:
    """Run shard *shard* of *manifest*; each item's outcome is ``value`` or ``error``, never both."""
    start, stop = manifest['shards'][shard]
    function = entry or load_entry(manifest['entry'])
    results = []
    for index in range(start, stop):
        try:
            results.append({'index': index, 'ok': True, 'value': function(manifest['items'][index])})
        except Exception:  # noqa: BLE001 -- an item's failure is recorded, not propagated; see the module docstring
            results.append({'index': index, 'ok': False, 'error': traceback.format_exc(limit=5)})
    return {'shard': shard, 'results': results}


def main(argv: list[str] | None = None) -> int:
    """Entry point of every array task."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != len(_ARGS):
        emit('usage: python -m lab_commons.hpc.worker <manifest.json> <shard>', err=True)
        return 2
    manifest_path = Path(args[0])
    shard = int(args[1])
    record = run_shard(json.loads(manifest_path.read_text(encoding='utf-8')), shard)
    target = manifest_path.parent / 'results' / f'{shard}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix('.json.part')
    partial.write_text(json.dumps(record) + '\n', encoding='utf-8')
    partial.replace(target)
    return 0


if __name__ == '__main__':
    sys.exit(main())
