"""What a run has MEASURED about a test tree, carried from one record to the next. Pure.

FOUR FACTS, EACH KEYED BY WHAT IT DESCRIBES. ``durations`` -- seconds per node id, the sum of its
phases as pytest timed them; ``floors`` -- seconds an id that NEVER FINISHED is known to take at least,
because it was still running when its item was killed; ``overheads`` -- seconds per test FILE that are
not any test's (the interpreter, the imports, module fixtures: an item's wall time minus its tests',
shared among the item's files so the shares sum back to what was measured); ``peaks_mb`` -- the peak RSS
of a file's pytest process. A newer measurement replaces an older one; nothing is averaged, because the
code under test changed between the two. A finished id drops its floor: its duration is now known.

An item's estimate is its files' overheads plus its ids' durations, each id at least its floor; an id
not yet timed in a file that has others is priced at that file's mean. A file with NOTHING measured is
priced at ``Cost.seconds`` and named, so a plan says how much of it is a guess.

AN ID THAT DIES IS PRICED FROM ITS DEATH, NOT FROM ITS SURVIVORS. Measured 2026-10-10 (a downstream
run): a file whose expensive ids kept being killed was priced at the mean of the cheap ids that finished,
so every retry was sized shorter than the wall the last one died at, and 325 ids were lost after three
rounds.

A PEAK READ AT A KILL IS THE LIMIT, NOT THE NEED. Measured 2026-10-13 (Ada): children OOM-killed at
1 GB read ~1120 MB; kept as measured, the next run would ask 1.5x the limit and die again. A signalled
item's peak is a FLOOR grown by :data:`OOM_GROWTH` -- what the retry asks -- and never lowers a peak
already known. The declared ``cost.mem_gb`` is a floor too: the peak is the pytest child's, and the
task's cgroup also holds the item runner (Ada 2026-10-12: a 150 MB peak priced 225M and 429M, OOM both).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from lab_commons.hpc.config import Cost, Policy

__all__ = ['OOM_GROWTH', 'Measured']

#: MB per GB, as the plan's memory is written.
_MB_PER_GB = 1024

#: How much more memory a run asks after an OUT_OF_MEMORY -- the next round's request and a killed peak's floor.
OOM_GROWTH: Final = 2


def _file(node: str) -> str:
    return node.split('::', maxsplit=1)[0]


@dataclass
class Measured:
    """The four measurements; :meth:`of` reads them from a record, :meth:`record` writes them back."""

    durations: dict[str, float] = field(default_factory=dict)
    floors: dict[str, float] = field(default_factory=dict)
    overheads: dict[str, float] = field(default_factory=dict)
    peaks_mb: dict[str, float] = field(default_factory=dict)

    @classmethod
    def of(cls, record: dict[str, Any]) -> Measured:
        """The measurements a previous run record carries; an older record without them gives none."""
        return cls(
            durations=dict(record.get('durations', {})),
            floors=dict(record.get('floors', {})),
            overheads=dict(record.get('overheads', {})),
            peaks_mb=dict(record.get('peaks_mb', {})),
        )

    def record(self) -> dict[str, dict[str, float]]:
        """The record's keys for these measurements."""
        return {
            'durations': self.durations,
            'floors': self.floors,
            'overheads': self.overheads,
            'peaks_mb': self.peaks_mb,
        }

    def learn(self, ids: Sequence[str], folded: dict[str, Any]) -> None:
        """Take one item's folded stream (:func:`lab_commons.hpc.pytest_item.read_stream`).

        An item may span several files and pays ONE wall; its overhead is SHARED equally among its files,
        so :meth:`estimate`, which sums the shares, gives the same item back what it measured. Its peak is
        recorded for every file, an upper bound for the file alone; a SIGNALLED item's peak is a floor
        (the module docstring).
        """
        self.durations.update(folded['seconds'])
        for node in folded['seconds']:
            self.floors.pop(node, None)
        done = folded['done']
        if done is None or not ids:
            return
        tests = sum(folded['seconds'].values())
        files = list(dict.fromkeys(_file(n) for n in ids))
        overhead = round(max(0.0, float(done['wall']) - tests) / len(files), 3)
        peak = done.get('peak_mb')
        signalled = int(done.get('done', 0)) < 0
        for name in files:
            self.overheads[name] = overhead
            if peak is None:
                continue
            if signalled:
                self.peaks_mb[name] = max(self.peaks_mb.get(name, 0.0), float(peak) * OOM_GROWTH)
            else:
                self.peaks_mb[name] = float(peak)

    def killed(self, ids: Sequence[str], folded: dict[str, Any], spent: float) -> None:
        """An item killed after *spent* seconds: its first unfinished id ran for what the rest does not explain.

        Pytest runs an item's ids in the order given, so the first id without an outcome is the one the
        kill interrupted. It took at least *spent* minus the finished ids' durations and the files' known
        overheads; that is its FLOOR. The ids after it never started and keep their estimate.
        """
        rest = [n for n in ids if n not in folded['outcomes']]
        if not rest or spent <= 0:
            return
        files = dict.fromkeys(_file(n) for n in ids)
        explained = sum(folded['seconds'].values()) + sum(self.overheads.get(f, 0.0) for f in files)
        floor = round(spent - explained, 3)
        if floor > 0:
            self.floors[rest[0]] = max(self.floors.get(rest[0], 0.0), floor)

    def _by_file(self) -> dict[str, list[float]]:
        by: dict[str, list[float]] = {}
        for node, seconds in self.durations.items():
            by.setdefault(_file(node), []).append(seconds)
        return by

    def _seconds(self, ids: Sequence[str], cost: Cost, by: dict[str, list[float]]) -> tuple[float, list[str]]:
        """One item: its files' overheads plus its tests, each at least its floor; unmeasured is ``cost.seconds``."""
        files: dict[str, list[str]] = {}
        for node in ids:
            files.setdefault(_file(node), []).append(node)
        tests, overhead, unmeasured = 0.0, 0.0, []
        for name, nodes in files.items():
            mates = by.get(name, [])
            floors = sum(self.floors.get(n, 0.0) for n in nodes)
            if not mates and name not in self.overheads:
                tests += max(cost.seconds, floors)
                unmeasured.append(name)
                continue
            mean = sum(mates) / len(mates) if mates else 0.0
            tests += sum(max(self.durations.get(n, mean), self.floors.get(n, 0.0)) for n in nodes)
            overhead += self.overheads.get(name, 0.0)
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
        """Whole files, consecutive, joined into items of up to *budget* estimated seconds -- one process each.

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
        """The worst measured peak of these items' files times ``policy.safety``, never under ``cost.mem_gb``.

        An unmeasured file is priced at ``cost.mem_gb`` alone, so it cannot mis-price the array.
        """
        files = {_file(n) for ids in items for n in ids}
        worst = max((self.peaks_mb.get(f, 0.0) for f in files), default=0.0) * policy.safety / _MB_PER_GB
        return max(worst, cost.mem_gb)
