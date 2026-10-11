"""Where a run's wall and core-hours went, from Slurm's own clock. Pure.

A SPEEDUP IS A NUMBER ONLY IF BOTH SIDES WERE MEASURED THE SAME WAY. Every round keeps a ``clock``
(:func:`round_clock`): queue wait, round wall, straggler tail, the core-seconds Slurm charged and the
seconds the item processes were busy. :func:`phases` folds the rounds and the login-node ``prep`` into
one breakdown; :func:`describe` is the line ``run gather`` prints.

Measured 2026-10-11 (a downstream full run, three rounds): 4.2 h wall, of which round 0 was 1.2 h and
the two retry rounds 3.0 h; 37 core-hours charged for 33 the processes were busy. None of that was in the
record, so no change could be priced.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

__all__ = ['Clock', 'describe', 'phases', 'round_clock']

_HOUR = 3600.0


@dataclass(frozen=True)
class Clock:
    """One array task as ``sacct`` saw it: state, epoch seconds (``None`` until known), elapsed, cpus."""

    state: str
    submit: float | None = None
    start: float | None = None
    end: float | None = None
    elapsed: float = 0.0
    cpus: int = 1


def round_clock(clocks: Mapping[int, Clock], busy: float) -> dict[str, float]:
    """One finished round: *clocks* per shard, *busy* the seconds its item processes reported."""
    tasks = list(clocks.values())
    submit = min((c.submit for c in tasks if c.submit is not None), default=0.0)
    starts = [c.start for c in tasks if c.start is not None]
    ends = [c.end for c in tasks if c.end is not None]
    elapsed = sorted(c.elapsed for c in tasks) or [0.0]
    return {
        'submit': submit,
        'end': max(ends, default=submit),
        'queue_s': statistics.median(s - submit for s in starts) if starts else 0.0,
        'wall_s': max(ends, default=submit) - submit,
        'tail_s': elapsed[-1] - statistics.median(elapsed),
        'core_s': sum(c.elapsed * c.cpus for c in tasks),
        'busy_s': busy,
    }


def phases(run: Mapping[str, Any]) -> dict[str, float]:
    """The breakdown of a run state or record: its ``prep`` seconds and every round's ``clock``.

    ``wall_s`` is prep plus first submit to last end; ``retry_s`` is what the rounds after the first
    added to it (their walls and the gaps before them); ``gap_s`` the time between a round's end and the
    next submit -- a gather nobody made. ``utilisation`` is busy over charged core-seconds.
    """
    clocks: Sequence[Mapping[str, float]] = [r['clock'] for r in run.get('rounds', []) if 'clock' in r]
    prep = float(sum(run.get('prep', {}).values()))
    if not clocks:
        return {'prep_s': prep}
    gaps = [b['submit'] - a['end'] for a, b in pairwise(clocks)]
    core = sum(c['core_s'] for c in clocks)
    busy = sum(c['busy_s'] for c in clocks)
    return {
        'prep_s': prep,
        'wall_s': prep + clocks[-1]['end'] - clocks[0]['submit'],
        'queue_s': sum(c['queue_s'] for c in clocks),
        'tail_s': sum(c['tail_s'] for c in clocks),
        'retry_s': sum(c['wall_s'] for c in clocks[1:]) + sum(gaps),
        'gap_s': sum(gaps),
        'core_h': core / _HOUR,
        'busy_h': busy / _HOUR,
        'utilisation': busy / core if core else 0.0,
    }


def describe(breakdown: Mapping[str, float]) -> str:
    """One line: wall and where it went, then core-hours and utilisation."""
    if 'wall_s' not in breakdown:
        return f'phases: prep {breakdown["prep_s"] / 60:.1f} min, no round finished yet'
    m = {k: v / 60 for k, v in breakdown.items() if k.endswith('_s')}
    return (
        f'phases: wall {m["wall_s"]:.0f} min = prep {m["prep_s"]:.1f} + queue {m["queue_s"]:.1f} + retries '
        f'{m["retry_s"]:.0f} (gaps {m["gap_s"]:.0f}) + the rest; straggler tails {m["tail_s"]:.0f} min; '
        f'{breakdown["core_h"]:.1f} core-h charged, {breakdown["busy_h"]:.1f} busy '
        f'({breakdown["utilisation"]:.0%})'
    )
