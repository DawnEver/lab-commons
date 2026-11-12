"""What one array task runs on a compute node: its shard of the manifest, one item per lane at a time.

``python -m lab_commons.hpc.worker <manifest.json> <shard index>`` runs the shard -- one item per lane of the
manifest's ``lanes`` at once (``[task] lane_domains``, cut by :mod:`lab_commons.hpc.topology`), each on its own
thread; without lanes, one item at a time. While an item runs, :func:`lane_env` is ITS lane's binding --
``LC_HPC_LANE``, ``LC_HPC_LANE_CPUS`` and ``LC_HPC_LANE_MEMS`` (cpulists) -- for the item to pass to what it
launches, so no entry hard-codes a core number::

    subprocess.run(['bash', '-c', 'taskset -c "$LC_HPC_LANE_CPUS" numactl --membind="$LC_HPC_LANE_MEMS" ./solve'],
                   env={**os.environ, **lane_env()})

(Lanes share one process, so the binding is per thread, never written to ``os.environ``.) On Linux the worker
also pins the lane's thread to its CPUs (``os.sched_setaffinity``) before the item runs, and every process the
item starts inherits it, so an item is CPU-bound even if it never reads :func:`lane_env`; memory binding is the
item's, through ``LC_HPC_LANE_MEMS``. Where the call does not exist the thread runs unpinned.
``python -m lab_commons.hpc.worker --close <manifest.json> <shard index> <exit code> <log>`` closes it.

ONE RESULT FILE PER JOB (a file is an HPC resource). Every array task of a run appends to the run's ONE
:data:`RESULTS` beside its manifest: an ``outcome`` line per item as it finishes, then -- written by the
task's script once the worker has exited, whatever its exit -- one ``shard`` line carrying the worker's
exit code and the tail of its output. The task's own stdout/stderr go to node-local ``$TMPDIR``, never to
the shared filesystem; only that tail is kept. Lines are :class:`lab_commons.outcomes.record.Event`
lines (the outcomes JSONL format, its schema and upgrade-on-read included).
:class:`~lab_commons.outcomes.record.Record` keeps ONE writer per file and a run has one writer per array
task, so each append here takes an exclusive lock on the file, cuts a killed writer's torn tail, and
appends one line, fsynced.

AN ITEM'S FAILURE IS DATA, A SHARD'S IS SLURM'S. Each item's line is an ``outcome`` event carrying a
:class:`~lab_commons.outcomes.Cell` (its returned value rides in the event's data): returned -> ``OK``, unless the
value reports a command's non-zero ``exit`` -> ``ERROR`` with reason ``exit``;
raised the declared :class:`Rejected` -> ``REJECTED`` with the reason it carries; raised the declared
:class:`Failed` -> ``ERROR`` with its reason AND the value it completed before failing (a campaign item's rows
so far ride back beside why it stopped); any other exception -> ``ERROR`` with its traceback and no value.
The shard carries on either way. A shard is RETURNED only by a ``shard`` line with exit 0; a shard killed by
Slurm (time, memory) leaves its finished items and no such line, and is re-run whole.
"""

from __future__ import annotations

import contextlib
import json
import os
import pkgutil
import queue
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.topology import cpu_ids
from lab_commons.log import emit
from lab_commons.outcomes import Cell, Reason, Status
from lab_commons.outcomes.jsonl import read_jsonl
from lab_commons.outcomes.record import OUTCOME, Event

__all__ = [
    'ITEM',
    'RESULTS',
    'SHARD',
    'TAIL_LINES',
    'Failed',
    'Rejected',
    'append_event',
    'close_shard',
    'lane_env',
    'load_entry',
    'main',
    'run_shard',
]

#: The run's one result file, beside its manifest.
RESULTS: Final = 'results.jsonl'

#: The event kinds of :data:`RESULTS`: one item's outcome (:data:`OUTCOME`), and a shard's close. A pre-Cell
#: worker wrote :data:`ITEM` lines (``ok`` / ``value`` / ``error`` data); they are still read.
ITEM: Final = 'item'
SHARD: Final = 'shard'

#: Lines of a task's output kept in its ``shard`` line.
TAIL_LINES: Final = 40

#: Windows locks a byte RANGE and blocks reads of it, so lock one far past any content (tests run there).
_FAR: Final = 1 << 40

#: ``--close`` and its four arguments; a run is the manifest and the shard.
_CLOSE_ARGS: Final = 5
_RUN_ARGS: Final = 2


class Rejected(Exception):
    """Raised by an entry point to DECIDE no: the item is ``REJECTED`` with *reason*, never retried."""

    def __init__(self, reason: Reason) -> None:
        """Carry the machine *reason* the item's cell records."""
        super().__init__(reason.text or reason.kind)
        self.reason = reason


class Failed(Exception):
    """Raised by an entry point that FAILED after completing work: ``ERROR`` with *reason*, *value* kept."""

    def __init__(self, reason: Reason, value: object) -> None:
        """Carry the machine *reason* the item's cell records and the *value* that rides back with it."""
        super().__init__(reason.text or reason.kind)
        self.reason = reason
        self.value = value


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


_LANE = threading.local()


def lane_env() -> dict[str, str]:
    """The running item's lane binding, as environment variables for what it launches; ``{}`` outside a lane."""
    return dict(getattr(_LANE, 'env', {}))


def _returned_cell(value: object) -> Cell:
    """A returned value's cell: OK, unless it reports a command that exited non-zero (``{'exit': N}``, N != 0).

    An item that ran a process and RETURNS its exit code has not succeeded when that code is non-zero; reading it
    as ``OK`` is a declaration that lies (a crashed run gathered as a result). Its value still rides back.
    """
    code = value.get('exit') if isinstance(value, dict) else None
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return Cell(Status.ERROR, reason=Reason('exit', f'the command exited {code}'))
    return Cell(Status.OK)


def run_shard(manifest: dict[str, Any], shard: int, results: Path, entry: Callable[[Any], Any] | None = None) -> int:
    """Run shard *shard* of *manifest*, appending each item's outcome to *results*; the item count."""
    start, stop = manifest['shards'][shard]
    function = entry or load_entry(manifest['entry'])

    lanes = manifest['lanes']
    free: queue.SimpleQueue[int] = queue.SimpleQueue()
    for lane in range(len(lanes)):
        free.put(lane)

    def one(index: int) -> None:
        if lanes:
            lane = free.get()
            _LANE.env = {
                'LC_HPC_LANE': str(lane),
                'LC_HPC_LANE_CPUS': lanes[lane]['cpus'],
                'LC_HPC_LANE_MEMS': lanes[lane]['mems'],
            }
            if hasattr(os, 'sched_setaffinity'):
                os.sched_setaffinity(0, set(cpu_ids(lanes[lane]['cpus'])))
        try:
            item(index)
        finally:
            if lanes:
                _LANE.env = {}
                free.put(lane)

    def item(index: int) -> None:
        data: dict[str, Any] = {'shard': shard}
        try:
            data['value'] = function(manifest['items'][index])
            cell = _returned_cell(data['value'])
        except Rejected as rejected:
            cell = Cell(Status.REJECTED, reason=rejected.reason)
        except Failed as failed:
            data['value'] = failed.value
            cell = Cell(Status.ERROR, reason=failed.reason)
        except Exception:  # noqa: BLE001 -- an item's failure is recorded, not propagated; see the module docstring
            cell = Cell(Status.ERROR, reason=Reason('exception', traceback.format_exc(limit=5)))
        append_event(results, Event(OUTCOME, str(index), cell=cell, data=data))

    with ThreadPoolExecutor(max_workers=max(1, len(lanes))) as pool:
        list(pool.map(one, range(start, stop)))
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
