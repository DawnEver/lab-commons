"""A verdict's RESULTS: streams folded into outcomes, and the record written to the caller's ``-o``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lab_commons.hpc.measured import Measured
from lab_commons.hpc.pytest_item import read_stream
from lab_commons.hpc.run import Runner

__all__ = ['fold', 'read_streams', 'summary', 'write_record']


def fold(
    items: list[dict[str, Any]],
    streams: dict[int, str],
    outcomes: dict[str, str],
    measured: Measured,
    reasons: dict[str, str],
) -> list[list[str]]:
    """Record one round's streams into *outcomes*, *reasons* and *measured*; return the unfinished ids, halved."""
    pending: list[list[str]] = []
    for index, item in enumerate(items):
        group = item['ids']
        folded = read_stream(streams.get(index, ''), group)
        outcomes.update(folded['outcomes'])
        reasons.update(folded['whys'])
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['outcomes']]
        if folded['done'] is not None:
            outcomes.update(dict.fromkeys(rest, 'missing'))
            continue
        half = (len(rest) + 1) // 2
        pending += [part for part in (rest[:half], rest[half:]) if part]
    return pending


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


def write_record(record: dict[str, Any], target: Path) -> None:
    """The record as JSON at *target* -- the caller's chosen path, created with its parent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, sort_keys=True), encoding='utf-8')
