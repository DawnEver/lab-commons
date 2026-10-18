"""What one array task runs on a compute node: its shard of the manifest, one item at a time.

``python -m lab_commons.hpc.worker <manifest.json> <shard index>`` runs the shard;
``python -m lab_commons.hpc.worker --close <manifest.json> <shard index> <exit code> <log>`` closes it.

ONE RESULT FILE PER JOB (a file is an HPC resource). Every array task of a run appends to the run's ONE
:data:`RESULTS` beside its manifest: an ``item`` line per item as it finishes, then -- written by the
task's script once the worker has exited, whatever its exit -- one ``shard`` line carrying the worker's
exit code and the tail of its output. The task's own stdout/stderr go to node-local ``$TMPDIR``, never to
the shared filesystem; only that tail is kept. Lines are :class:`lab_commons.outcomes.record.Event`
lines (the outcomes JSONL format, its schema and upgrade-on-read included).
:class:`~lab_commons.outcomes.record.Record` keeps ONE writer per file and a run has one writer per array
task, so each append here takes an exclusive lock on the file, cuts a killed writer's torn tail, and
appends one line, fsynced.

AN ITEM'S FAILURE IS DATA, A SHARD'S IS SLURM'S. An exception raised by the entry point is recorded
against that item and the shard carries on. A shard is RETURNED only by a ``shard`` line with exit 0; a
shard killed by Slurm (time, memory) leaves its finished items and no such line, and is re-run whole.
"""

from __future__ import annotations

import contextlib
import json
import os
import pkgutil
import sys
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Final

from lab_commons.log import emit
from lab_commons.outcomes.jsonl import read_jsonl
from lab_commons.outcomes.record import Event

__all__ = ['ITEM', 'RESULTS', 'SHARD', 'TAIL_LINES', 'append_event', 'close_shard', 'load_entry', 'main', 'run_shard']

#: The run's one result file, beside its manifest.
RESULTS: Final = 'results.jsonl'

#: The event kinds of :data:`RESULTS`: one item's outcome, and a shard's close.
ITEM: Final = 'item'
SHARD: Final = 'shard'

#: Lines of a task's output kept in its ``shard`` line.
TAIL_LINES: Final = 40

#: Windows locks a byte RANGE and blocks reads of it, so lock one far past any content (tests run there).
_FAR: Final = 1 << 40

#: ``--close`` and its four arguments; a run is the manifest and the shard.
_CLOSE_ARGS: Final = 5
_RUN_ARGS: Final = 2


def load_entry(spec: str) -> Callable[[Any], Any]:
    """``package.module:function`` to the callable it names."""
    module, sep, name = spec.partition(':')
    if not sep or not module or not name:
        msg = f'an entry point is "module:function", got {spec!r}'
        raise ValueError(msg)
    return pkgutil.resolve_name(spec)


@contextlib.contextmanager
def _exclusive(path: Path) -> Iterator[Any]:
    """*path* opened for appending under an exclusive lock held across the cluster's tasks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt  # noqa: PLC0415 -- the platform's own lock

            handle.seek(_FAR)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # noqa: PLC0415 -- the platform's own lock

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield handle


def append_event(path: Path, event: Event) -> None:
    """Append *event* to *path* as one durable line, cutting a killed writer's unterminated tail first."""
    line = (json.dumps(event.to_plain(), sort_keys=True, separators=(',', ':')) + '\n').encode()
    with _exclusive(path) as handle:
        end = handle.seek(0, os.SEEK_END)
        if end:
            handle.seek(end - 1)
            if handle.read(1) != b'\n':
                handle.truncate(read_jsonl(path).good_bytes)
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def run_shard(manifest: dict[str, Any], shard: int, results: Path, entry: Callable[[Any], Any] | None = None) -> int:
    """Run shard *shard* of *manifest*, appending each item's ``value`` or ``error`` to *results*; the item count."""
    start, stop = manifest['shards'][shard]
    function = entry or load_entry(manifest['entry'])
    for index in range(start, stop):
        try:
            data = {'shard': shard, 'ok': True, 'value': function(manifest['items'][index])}
        except Exception:  # noqa: BLE001 -- an item's failure is recorded, not propagated; see the module docstring
            data = {'shard': shard, 'ok': False, 'error': traceback.format_exc(limit=5)}
        append_event(results, Event(ITEM, str(index), data=data))
    return stop - start


def close_shard(results: Path, shard: int, code: int, log: Path) -> None:
    """Append shard *shard*'s close: the worker's exit *code* and the last :data:`TAIL_LINES` of its *log*."""
    try:
        tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-TAIL_LINES:]
    except OSError:
        tail = []
    append_event(results, Event(SHARD, str(shard), data={'code': code, 'tail': tail}))


def main(argv: list[str] | None = None) -> int:
    """Entry point of every array task: run a shard, or ``--close`` it."""
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ['--close'] and len(args) == _CLOSE_ARGS:
        manifest, shard, code, log = args[1:]
        close_shard(Path(manifest).parent / RESULTS, int(shard), int(code), Path(log))
        return 0
    if len(args) != _RUN_ARGS:
        emit('usage: python -m lab_commons.hpc.worker [--close] <manifest.json> <shard> [<code> <log>]', err=True)
        return 2
    path = Path(args[0])
    run_shard(json.loads(path.read_text(encoding='utf-8')), int(args[1]), path.parent / RESULTS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
