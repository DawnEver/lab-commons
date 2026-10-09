"""What a verdict has MEASURED about a test tree, carried from one record to the next. Pure.

THREE FACTS, EACH KEYED BY WHAT IT DESCRIBES. ``durations`` -- seconds per node id, the sum of its
phases as pytest timed them; ``overheads`` -- seconds per test FILE that are not any test's (the
interpreter, the imports: an item's wall time minus its tests'); ``peaks_mb`` -- the peak RSS of a
file's pytest process. A newer measurement replaces an older one; nothing is averaged, because the code
under test changed between the two.

An item's estimate is its file's overhead plus its ids' durations; an id not yet timed in a file that
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
        """Take one item's folded stream (:func:`lab_commons.hpc.pytest_item.read_stream`)."""
        self.durations.update(folded['seconds'])
        done = folded['done']
        if done is None or not ids:
            return
        name = _file(ids[0])
        tests = sum(folded['seconds'].values())
        self.overheads[name] = round(max(0.0, float(done['wall']) - tests), 3)
        if done.get('peak_mb') is not None:
            self.peaks_mb[name] = float(done['peak_mb'])

    def estimate(self, items: Sequence[Sequence[str]], cost: Cost) -> tuple[list[float], list[str]]:
        """Seconds per item, and the files priced at ``cost.seconds`` because nothing of them was measured."""
        seconds, unmeasured = [], []
        for ids in items:
            name = _file(ids[0])
            known = [self.durations[n] for n in ids if n in self.durations]
            mates = [s for n, s in self.durations.items() if _file(n) == name] if len(known) < len(ids) else known
            if not mates and name not in self.overheads:
                seconds.append(cost.seconds)
                unmeasured.append(name)
                continue
            mean = sum(mates) / len(mates) if mates else 0.0
            seconds.append(self.overheads.get(name, 0.0) + sum(self.durations.get(n, mean) for n in ids))
        return seconds, sorted(set(unmeasured))

    def mem_gb(self, items: Sequence[Sequence[str]], cost: Cost, policy: Policy) -> float:
        """The worst measured peak of these items' files times ``policy.safety``; ``cost.mem_gb`` covers the rest."""
        files = {_file(ids[0]) for ids in items}
        peaks = [self.peaks_mb[f] for f in files if f in self.peaks_mb]
        worst = max(peaks, default=0.0) * policy.safety / _MB_PER_GB
        return worst if len(peaks) == len(files) and worst > 0 else max(worst, cost.mem_gb)
