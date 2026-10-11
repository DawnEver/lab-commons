"""A run's RESULTS: streams folded into outcomes, and the record ``run-<sha>-<platform>-<req>.json.gz``."""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.measured import Measured
from lab_commons.hpc.pytest_item import read_stream

__all__ = ['fold', 'read_record', 'read_streams', 'record_name', 'summary', 'write_record']


def fold(
    items: list[dict[str, Any]],
    streams: dict[int, str],
    outcomes: dict[str, str],
    measured: Measured,
    reasons: dict[str, str],
    *,
    shards: Sequence[Sequence[int]],
    walls: Sequence[float],
) -> tuple[list[list[str]], bool, float]:
    """Record one round's streams; return the unfinished ids, halved, whether an item was KILLED, and busy seconds.

    *busy* is the sum of every closed item's own wall -- what the processes used of the cores charged.

    An item closed by a signal (``done`` < 0: the cgroup's OOM killer, a timeout) did not finish its ids;
    they are retried, halved, like an item that never closed -- never stamped ``missing``.

    WHAT A KILL COST IS MEASURED TOO. A shard (*shards*: item ranges) runs its items back to back for
    the seconds Slurm says it ran (*walls*, one per shard), so the first item of a shard that never closed
    was killed after that minus what the items before it took; a signalled item says its own wall.
    Measured 2026-10-11: a shard killed after 7 s was charged its whole 216-min PLANNED wall, so one id
    carried a 12960 s floor it never ran. That time goes to
    :meth:`Measured.killed`, so the id the kill interrupted is never re-priced below it.
    """
    pending: list[list[str]] = []
    killed = False
    folds = [read_stream(streams.get(index, ''), item['ids']) for index, item in enumerate(items)]
    spent = _killed_after(folds, shards, walls)
    for index, (item, folded) in enumerate(zip(items, folds, strict=True)):
        group = item['ids']
        outcomes.update(folded['outcomes'])
        reasons.update(folded['whys'])
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['outcomes']]
        signalled = folded['done'] is not None and int(folded['done'].get('done', 0)) < 0
        killed = killed or signalled
        if folded['done'] is not None and not signalled:
            outcomes.update(dict.fromkeys(rest, 'missing'))
            continue
        measured.killed(group, folded, spent.get(index, 0.0))
        half = (len(rest) + 1) // 2
        pending += [part for part in (rest[:half], rest[half:]) if part]
    busy = sum(float(f['done']['wall']) for f in folds if f['done'] is not None)
    return pending, killed, busy


def _killed_after(
    folds: list[dict[str, Any]], shards: Sequence[Sequence[int]], walls: Sequence[float]
) -> dict[int, float]:
    """Seconds each killed item ran: its own ``done`` wall when signalled, else what its shard's wall had left."""
    spent: dict[int, float] = {}
    for (start, stop), wall in zip(shards, walls, strict=True):
        left = wall
        for index in range(start, stop):
            done = folds[index]['done']
            if done is None:
                spent[index] = max(0.0, left)
                break
            if int(done.get('done', 0)) < 0:
                spent[index] = float(done['wall'])
            left -= float(done['wall'])
    return spent


def read_streams(run: Runner, directory: str) -> dict[int, str]:
    """Every item stream under *directory* (``<index>.jsonl``), keyed by item index -- one remote command."""
    listing = 'for f in *.jsonl; do [ -e "$f" ] && echo "@@@ $f" && cat "$f"; done'
    out = run(f'cd {directory} 2>/dev/null && {listing}; true', None)
    streams: dict[int, list[str]] = {}
    current: list[str] = []
    for line in out.splitlines():
        if line.startswith('@@@ ') and line.endswith('.jsonl'):
            current = streams.setdefault(int(line[4:-6]), [])
        else:
            current.append(line)
    return {index: '\n'.join(lines) for index, lines in streams.items()}


def summary(record: dict[str, Any]) -> str:
    """``passed=…, failed=…`` counts of a record, worst first."""
    counts: dict[str, int] = {}
    for outcome in record['outcomes'].values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return ', '.join(f'{k}={v}' for k, v in sorted(counts.items()))


def record_name(record: dict[str, Any]) -> str:
    """``run-<sha>-<platform>-<req>.json.gz`` -- one run of one sha on one platform, one request."""
    return f'run-{record["sha"]}-{record["platform"]}-{record["req"]}.json.gz'


def write_record(record: dict[str, Any], directory: Path) -> Path:
    """The record gzipped under *directory* (created) at :func:`record_name`; returns its path."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / record_name(record)
    partial = target.with_name(target.name + '.partial')  # atomic: a crash never leaves half a record
    partial.write_bytes(gzip.compress(json.dumps(record, sort_keys=True).encode('utf-8'), mtime=0))
    partial.replace(target)
    return target


def read_record(path: Path) -> dict[str, Any]:
    """The record :func:`write_record` wrote."""
    return json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
