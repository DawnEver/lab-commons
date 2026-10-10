"""A run's RESULTS: streams folded into outcomes, and the record ``run-<sha>-<platform>-<req>.json.gz``."""

from __future__ import annotations

import gzip
import json
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
) -> tuple[list[list[str]], bool]:
    """Record one round's streams; return the unfinished ids, halved, and whether an item was KILLED.

    An item closed by a signal (``done`` < 0: the cgroup's OOM killer, a timeout) did not finish its ids;
    they are retried, halved, like an item that never closed -- never stamped ``missing``.
    """
    pending: list[list[str]] = []
    killed = False
    for index, item in enumerate(items):
        group = item['ids']
        folded = read_stream(streams.get(index, ''), group)
        outcomes.update(folded['outcomes'])
        reasons.update(folded['whys'])
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['outcomes']]
        signalled = folded['done'] is not None and int(folded['done'].get('done', 0)) < 0
        killed = killed or signalled
        if folded['done'] is not None and not signalled:
            outcomes.update(dict.fromkeys(rest, 'missing'))
            continue
        half = (len(rest) + 1) // 2
        pending += [part for part in (rest[:half], rest[half:]) if part]
    return pending, killed


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
    target.write_bytes(gzip.compress(json.dumps(record, sort_keys=True).encode('utf-8'), mtime=0))
    return target


def read_record(path: Path) -> dict[str, Any]:
    """The record :func:`write_record` wrote."""
    return json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
