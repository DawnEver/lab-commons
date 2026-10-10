"""What a verdict has MEASURED about a test tree, carried from one record to the next. Pure.

THREE FACTS, EACH KEYED BY WHAT IT DESCRIBES. ``durations`` -- seconds per node id, the sum of its
phases as pytest timed them; ``overheads`` -- seconds per test FILE that are not any test's (the
interpreter, the imports: an item's wall time minus its tests'); ``peaks_mb`` -- the peak RSS of a
file's pytest process. A newer measurement replaces an older one; nothing is averaged, because the code
under test changed between the two.

An item's estimate is ONE overhead (the worst of its files') plus its ids' durations; an id not yet timed in a file that
has others is priced at that file's mean. A file with NOTHING measured is priced at ``Cost.seconds`` and
named, so a plan says how much of it is a guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from lab_commons.hpc.config import Cost, Policy

__all__ = ['Measured']

#: MB per GB, as the plan's memory is written.
_MB_PER_GB = 1024


def _file(node: str) -> str:
    return node.split('::', maxsplit=1)[0]


@dataclass
class Measured:
    """The three measurements; :meth:`of` reads them from a record, :meth:`record` writes them back."""

    durations: dict[str, float] = field(default_factory=dict)
    overheads: dict[str, float] = field(default_factory=dict)
    peaks_mb: dict[str, float] = field(default_factory=dict)

    @classmethod
    def of(cls, record: dict[str, Any]) -> Measured:
        """The measurements a previous verdict record carries; an older record without them gives none."""
        return cls(
            durations=dict(record.get('durations', {})),
            overheads=dict(record.get('overheads', {})),
            peaks_mb=dict(record.get('peaks_mb', {})),
        )

    def record(self) -> dict[str, dict[str, float]]:
        """The record's keys for these measurements."""
        return {'durations': self.durations, 'overheads': self.overheads, 'peaks_mb': self.peaks_mb}

    def learn(self, ids: Sequence[str], folded: dict[str, Any]) -> None:
        """Take one item's folded stream (:func:`lab_commons.hpc.pytest_item.read_stream`).

        An item may span several files and pays ONE start-up; every file it held is recorded with that
        item's overhead and peak, an upper bound for the file alone, which :meth:`estimate` takes the max of.
        """
        self.durations.update(folded['seconds'])
        done = folded['done']
        if done is None or not ids:
            return
        tests = sum(folded['seconds'].values())
        overhead = round(max(0.0, float(done['wall']) - tests), 3)
        for name in dict.fromkeys(_file(n) for n in ids):
            self.overheads[name] = overhead
            if done.get('peak_mb') is not None:
                self.peaks_mb[name] = float(done['peak_mb'])

    def _by_file(self) -> dict[str, list[float]]:
        by: dict[str, list[float]] = {}
        for node, seconds in self.durations.items():
            by.setdefault(_file(node), []).append(seconds)
        return by

    def _seconds(self, ids: Sequence[str], cost: Cost, by: dict[str, list[float]]) -> tuple[float, list[str]]:
        """One item: ONE start-up (its files' worst overhead) plus its tests; an unmeasured file is ``cost.seconds``."""
        files: dict[str, list[str]] = {}
        for node in ids:
            files.setdefault(_file(node), []).append(node)
        tests, overhead, unmeasured = 0.0, 0.0, []
        for name, nodes in files.items():
            mates = by.get(name, [])
            if not mates and name not in self.overheads:
                tests += cost.seconds
                unmeasured.append(name)
                continue
            mean = sum(mates) / len(mates) if mates else 0.0
            tests += sum(self.durations.get(n, mean) for n in nodes)
            overhead = max(overhead, self.overheads.get(name, 0.0))
        return overhead + tests, unmeasured

    def estimate(self, items: Sequence[Sequence[str]], cost: Cost) -> tuple[list[float], list[str]]:
        """Seconds per item, and the files priced at ``cost.seconds`` because nothing of them was measured."""
        by = self._by_file()
        seconds, unmeasured = [], []
        for ids in items:
            spent, guessed = self._seconds(ids, cost, by)
            seconds.append(spent)
            unmeasured += guessed
        return seconds, sorted(set(unmeasured))

    def pack(self, files: Sequence[Sequence[str]], cost: Cost, budget: float) -> list[list[str]]:
        """Whole files, consecutive, joined into items of up to *budget* estimated seconds -- one start-up each.

        A file is never split (its ids, and any xdist ``@group`` among them, stay in one process); a file
        over *budget* is an item alone. Measured 2026-10-10: 4316 one-file items paid 9.5 h of start-up for
        6.0 h of tests.
        """
        by = self._by_file()
        items: list[list[str]] = []
        for ids in files:
            if items and self._seconds([*items[-1], *ids], cost, by)[0] <= budget:
                items[-1] = [*items[-1], *ids]
            else:
                items.append(list(ids))
        return items

    def mem_gb(self, items: Sequence[Sequence[str]], cost: Cost, policy: Policy) -> float:
        """The worst measured peak of these items' files times ``policy.safety``; ``cost.mem_gb`` covers the rest."""
        files = {_file(n) for ids in items for n in ids}
        peaks = [self.peaks_mb[f] for f in files if f in self.peaks_mb]
        worst = max(peaks, default=0.0) * policy.safety / _MB_PER_GB
        return worst if len(peaks) == len(files) and worst > 0 else max(worst, cost.mem_gb)
