"""THE PER-TEST FACTS of the measured demand table (:mod:`lab_commons.compute.learned`): one item, priced.

PER TEST, EACH FACT KEYED BY WHAT IT DESCRIBES. ``durations`` -- seconds per node id, the sum of its
phases as pytest timed them; ``floors`` -- seconds an id that NEVER FINISHED is known to take at least,
because it was still running when its item was killed; ``overheads`` -- seconds per test FILE that are
not any test's (the interpreter, the imports, module fixtures: an item's wall time minus its tests',
shared among the item's files so the shares sum back to what was measured); ``peaks_mb`` -- the peak RSS
of a file's pytest process; ``id_peaks_mb`` -- MB an id that never finished is known to need at least, the
memory twin of ``floors``: one heavy id of a light parametrized file is priced by its own floor, never the
file's (run 4fb97faa904f: sdm cases needing 2.1-6.4 GB in a file whose peak read 1351 MB).
A newer measurement replaces an older one; nothing is averaged, because the code under test changed between
the two. A finished id drops its floors: its duration and peak are now known.

A KILLED WALL IS NO OVERHEAD. Measured 2026-10-30 (run 4fb97faa904f): an item OOM-killed 498 s into one id booked
the 498 s as its file's overhead, which :meth:`Measured.killed` then subtracted as explained, so the id learned a
38.3 s floor; its retry was sized at minutes and 7 of the round's 8 failures were TIMEOUTs. Only an item that
closed by itself measures overhead; a killed one floors its cut id at the time it ran, grown as for a TIMEOUT.

An item's estimate is its files' overheads plus its ids' durations, each id at least its floor; an id
not yet timed in a file that has others is priced at that file's mean. A file with NOTHING measured is
priced at ``Cost.seconds`` and named, so a plan says how much of it is a guess.

AN ID THAT DIES IS PRICED FROM ITS DEATH, NOT FROM ITS SURVIVORS. Measured 2026-10-10 (a downstream
run): a file whose expensive ids kept being killed was priced at the mean of the cheap ids that finished,
so every retry was sized shorter than the wall the last one died at, and 325 ids were lost after three
rounds.

A PEAK READ AT A KILL IS THE LIMIT, NOT THE NEED, AND IT IS NO FILE'S PEAK. Measured 2026-10-13 (Ada):
children OOM-killed at 1 GB read ~1120 MB. A signalled item's need is more than that read: it is FLOORED at
the read times :data:`OOM_GROWTH`, never lowering a peak already known -- a ONE-FILE item's on its file, a
MULTI-file item's on the id it cut alone (``id_peaks_mb``): which file held the memory is unknown, and its
retry is bisected (:mod:`lab_commons.hpc.run`) until one file says. Measured 2026-10-30 (run 4fb97faa904f):
an ungrown read was asked again at the read times the safety, ~1.5x a run, and killed again run after run.
The growth is applied ONCE: the kill's taught floor is the growth, so the round's own growth
(:func:`lab_commons.hpc.gather.grown_after`) skips what a kill taught. Measured 2026-10-16 (Ada): a read grown
twice re-asked a 20 GB kill at 120 GB, and taught 240 GB to each of 56 files of ONE multi-file item.
The declared ``cost.mem_gb`` is a floor too: the peak is the pytest child's, and the
task's cgroup also holds the item runner (Ada 2026-10-12: a 150 MB peak priced 225M and 429M, OOM both).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from lab_commons.hpc.config import Cost, Policy

__all__ = ['ACCUMULATED_CLASSES', 'OOM_GROWTH', 'TIMEOUT_GROWTH', 'Measured', 'file_of', 'memory_band', 'task_gb']

#: MB per GB, as the plan's memory is written.
_MB_PER_GB = 1024

#: How much more memory an OOM-KILLED item's need is than the limit it died at: a killed peak's floor, and the
#: next round's ask of the id the kill cut.
OOM_GROWTH: Final = 2

#: How much more wall a TIMED-OUT item's next plan asks than the wall it died at, held to the partition max.
TIMEOUT_GROWTH: Final = 2

#: How many memory classes accumulation may lift a packed item above its files' own class: one, so packing never
#: more than doubles a class's ask, against the start-up a one-file item pays (measured 2026-10-10: 9.5 h of it).
ACCUMULATED_CLASSES: Final = 1


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def file_of(node: str) -> str:
    """The test file of node id *node*."""
    return node.split('::', maxsplit=1)[0]


@dataclass
class Measured:
    """The five measurements; :meth:`of` reads them from a record, :meth:`record` writes them back."""

    durations: dict[str, float] = field(default_factory=dict)
    floors: dict[str, float] = field(default_factory=dict)
    overheads: dict[str, float] = field(default_factory=dict)
    peaks_mb: dict[str, float] = field(default_factory=dict)
    id_peaks_mb: dict[str, float] = field(default_factory=dict)
    #: The files this instance -- ONE round's fold -- already learned a peak for: within a round a file's peak is
    #: the worst of its items, so a finished sibling never lowers what a killed one read (Ada 2026-10-24).
    _round: set[str] = field(default_factory=set, repr=False, compare=False)
    #: What this round's kills taught a grown peak floor -- a one-file item's file, a multi-file item's cut id.
    taught: set[str] = field(default_factory=set, repr=False, compare=False)

    @classmethod
    def of(cls, record: dict[str, Any]) -> Measured:
        """The measurements a previous run record carries; an older record without them gives none."""
        return cls(
            durations=dict(record.get('durations', {})),
            floors=dict(record.get('floors', {})),
            overheads=dict(record.get('overheads', {})),
            peaks_mb=dict(record.get('peaks_mb', {})),
            id_peaks_mb=dict(record.get('id_peaks_mb', {})),
        )

    def record(self) -> dict[str, dict[str, float]]:
        """The record's keys for these measurements."""
        return {
            'durations': self.durations,
            'floors': self.floors,
            'overheads': self.overheads,
            'peaks_mb': self.peaks_mb,
            'id_peaks_mb': self.id_peaks_mb,
        }

    def learn(self, ids: Sequence[str], folded: dict[str, Any]) -> None:
        """Take one item's folded stream (:func:`lab_commons.hpc.pytest_item.read_stream`).

        An item may span several files and pays ONE wall; its overhead is SHARED equally among its files,
        so :meth:`estimate`, which sums the shares, gives the same item back what it measured. Its peak is
        recorded for every file, an upper bound for the file alone; a SIGNALLED item's peak grown by
        :data:`OOM_GROWTH` is a floor of its file when it holds ONE, and of the id it cut when it holds several
        (the module docstring).
        """
        self.durations.update(folded['seconds'])
        for node in folded['seconds']:
            self.floors.pop(node, None)
            self.id_peaks_mb.pop(node, None)
        done = folded['done']
        if done is None or not ids:
            return
        tests = sum(folded['seconds'].values())
        files = list(dict.fromkeys(file_of(n) for n in ids))
        peak = done.get('peak_mb')
        signalled = int(done.get('done', 0)) < 0
        if not signalled:  # a killed wall holds the cut id's run, not the files' overhead (the module docstring)
            overhead = round(max(0.0, float(done['wall']) - tests) / len(files), 3)
            for name in files:
                self.overheads[name] = overhead
        if peak is None:
            return
        if signalled:
            self._killed_peak(ids, folded, files, float(peak) * OOM_GROWTH)
            return
        for name in files:
            floor = self.peaks_mb.get(name, 0.0) if name in self._round else 0.0
            self.peaks_mb[name] = max(floor, float(peak))
            self._round.add(name)

    def _killed_peak(self, ids: Sequence[str], folded: dict[str, Any], files: list[str], need: float) -> None:
        """Floor a killed item's *need* (MB): on its one file, else on the id it cut; never lower a known floor."""
        if len(files) == 1:
            self.peaks_mb[files[0]] = max(self.peaks_mb.get(files[0], 0.0), need)
            self._round.add(files[0])
            self.taught.add(files[0])
            return
        cut = next((n for n in ids if n not in folded['cells']), None)
        if cut is not None:
            self.id_peaks_mb[cut] = max(self.id_peaks_mb.get(cut, 0.0), need)
            self.taught.add(cut)

    def killed(
        self,
        ids: Sequence[str],
        folded: dict[str, Any],
        spent: float,
        *,
        timed_out: bool = False,
        top: float | None = None,
    ) -> None:
        """An item killed after *spent* seconds: its first unfinished id ran for what the rest does not explain.

        Pytest runs an item's ids in the order given, so the first id without a cell is the one the
        kill interrupted. It took at least *spent* minus the finished ids' durations and the files' known
        overheads; that is its FLOOR -- times :data:`TIMEOUT_GROWTH` when the wall cut it (*timed_out*) or a signal
        closed it (its ``done`` line below 0, the cgroup's OOM killer): it would not have finished within it --
        held to *top*, the longest wall any partition grants (seconds): past it is a guess no
        partition can host. After an OOM the ids after it never started and keep their estimate; after a
        TIMEOUT the item as a whole is re-priced at no less than *spent* times :data:`TIMEOUT_GROWTH`, held to
        *top* (:meth:`_outgrow`) -- dev-lab #493: only the cut id grew, and a 57 min kill was re-asked short and
        killed again at 110 min.
        """
        rest = [n for n in ids if n not in folded['cells']]
        if not rest or spent <= 0:
            return
        files = dict.fromkeys(file_of(n) for n in ids)
        explained = sum(folded['seconds'].values()) + sum(self.overheads.get(f, 0.0) for f in files)
        signalled = folded['done'] is not None and int(folded['done'].get('done', 0)) < 0
        floor = round((spent - explained) * (TIMEOUT_GROWTH if timed_out or signalled else 1), 3)
        floor = floor if top is None else min(floor, top)
        if floor > 0:
            self.floors[rest[0]] = max(self.floors.get(rest[0], 0.0), floor)
        if timed_out:
            self._outgrow(rest, spent * TIMEOUT_GROWTH if top is None else min(spent * TIMEOUT_GROWTH, top))

    def _outgrow(self, rest: Sequence[str], target: float) -> None:
        """Raise the floors of *rest* -- a timed-out item's unfinished ids -- until they price at least *target*.

        The ids after the cut never started, so their own estimates say nothing about the wall the item died
        at; the shortfall is spread evenly over them as FLOORS, the fact the record carries and every host's
        table folds (``learned.learn_record``), so the next plan of these ids asks no less than the wall grown.
        """
        by = self._byfile_of()
        prices = {n: max(self.durations.get(n, _mean(by.get(file_of(n), []))), self.floors.get(n, 0.0)) for n in rest}
        short = target - sum(prices.values())
        if short > 0:
            for node, price in prices.items():
                self.floors[node] = round(price + short / len(rest), 3)

    def _byfile_of(self) -> dict[str, list[float]]:
        by: dict[str, list[float]] = {}
        for node, seconds in self.durations.items():
            by.setdefault(file_of(node), []).append(seconds)
        return by

    def _seconds(self, ids: Sequence[str], cost: Cost, by: dict[str, list[float]]) -> tuple[float, list[str]]:
        """One item: its files' overheads plus its tests, each at least its floor; unmeasured is ``cost.seconds``."""
        files: dict[str, list[str]] = {}
        for node in ids:
            files.setdefault(file_of(node), []).append(node)
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
        by = self._byfile_of()
        seconds, unmeasured = [], []
        for ids in items:
            spent, guessed = self._seconds(ids, cost, by)
            seconds.append(spent)
            unmeasured += guessed
        return seconds, sorted(set(unmeasured))

    def pack(
        self,
        files: Sequence[Sequence[str]],
        cost: Cost,
        budget: float,
        band: Callable[[Sequence[str]], int] | None = None,
    ) -> list[list[str]]:
        """Whole files, joined into items of up to *budget* estimated seconds -- one process each.

        A file is never split (its ids, and any xdist ``@group`` among them, stay in one process); a file
        over *budget* is an item alone. Measured 2026-10-10: 4316 one-file items paid 9.5 h of start-up for
        6.0 h of tests.

        With *band* (a file's memory class, :func:`memory_band`), files are grouped by it -- collection order
        kept within a band -- and only files of ONE band share an item, so one heavy file never sizes the light
        files beside it. Measured 2026-10-19 (Ada): every task of a part asked 12-47 GB on one core because the
        array's memory was its heaviest file's. And since an item's memory ACCUMULATES (:meth:`item_gb`), a file
        joins only while the item's own class stays within :data:`ACCUMULATED_CLASSES` of its files' -- dev-lab #493:
        121 files of one 0.5-0.8 GB class shared one process that was killed at 1-2 GB.
        """
        by = self._byfile_of()
        ordered = sorted(files, key=band) if band is not None else list(files)
        items: list[list[str]] = []
        last = None
        for ids in ordered:
            key = band(ids) if band is not None else None
            joined = [*items[-1], *ids] if items else []
            fits = band is None or key is None or band(joined) <= key + ACCUMULATED_CLASSES
            if items and key == last and fits and self._seconds(joined, cost, by)[0] <= budget:
                items[-1] = [*items[-1], *ids]
            else:
                items.append(list(ids))
            last = key
        return items

    def item_gb(
        self, ids: Iterable[str], cost: Cost, policy: Policy, grown: Mapping[str, float] | None = None
    ) -> float:
        """ONE ITEM's memory: ONE pytest process over all its files, so the files ACCUMULATE.

        THE MODEL: each file's peak (learned, else :meth:`prior_mb`, raised to its ids' peak floors; times *grown*,
        the run's OOM growth of the id a kill cut, applied to that id's peak already held to the ``cost.mem_gb``
        floor, so a lone id asks ``max(peak x safety, floor) x growth``) is a process of the shared base --
        interpreter, pytest, common imports -- plus what the file adds.
        The base is the item's lightest peak; the item holds the worst peak plus every other
        distinct peak's growth over the base, times the safety, never under ``cost.mem_gb``. Equal peaks count
        ONCE: a finished multi-file item teaches its one read to every file (:meth:`learn`), one measurement.
        Upper-bounded, not averaged -- modules imported by two files are paid once and counted twice -- because
        dev-lab #493 (one account, 7 days) measured the worst-file ask too SMALL: ~10 tasks a run killed at 1-2 GB
        running 8-121 files with learned peaks of 0.5-0.8 GB. :meth:`pack` bounds the sum it makes.
        """
        floor_mb = cost.mem_gb * _MB_PER_GB / policy.safety
        files = self._file_peaks_mb(ids, floor_mb=floor_mb, grown=grown or {})
        peaks = sorted(set(files.values()), reverse=True)
        held = peaks[0] + sum(p - peaks[-1] for p in peaks[1:]) if peaks else 0.0
        return task_gb(held / _MB_PER_GB, prior_gb=cost.mem_gb, safety=policy.safety)

    def _file_peaks_mb(
        self, ids: Iterable[str], *, floor_mb: float = 0.0, grown: Mapping[str, float] | None = None
    ) -> dict[str, float]:
        """Each file of *ids* at its worst id.

        An id needs its file's peak (learned, else :meth:`prior_mb`) or its own peak floor, never under
        *floor_mb*, times its *grown*.
        """
        growth = grown or {}
        prior: dict[str, float] = {}
        peaks: dict[str, float] = {}
        for node in ids:
            name = file_of(node)
            if name not in prior:
                prior[name] = self.peaks_mb[name] if name in self.peaks_mb else (self.prior_mb(name) or 0.0)
            need = max(prior[name], self.id_peaks_mb.get(node, 0.0), floor_mb) * growth.get(node, 1.0)
            peaks[name] = max(peaks.get(name, 0.0), need)
        return peaks

    def prior_mb(self, name: str) -> float | None:
        """The PRIOR peak of an unmeasured file *name* (D5): its measured siblings' median, else the table's.

        Siblings are the measured files of its own directory; a directory with none falls back to the median
        of the whole table (the need-set the table describes). Never the table's maximum: one heavy file must
        not price every new file as heavy. ``None`` when nothing is measured at all.
        """
        folder = name.rpartition('/')[0]
        siblings = [mb for f, mb in self.peaks_mb.items() if f.rpartition('/')[0] == folder]
        pool = siblings or list(self.peaks_mb.values())
        return statistics.median(pool) if pool else None

    def peak_gb(self, nodes: Iterable[str]) -> float:
        """The worst MEASURED peak of these nodes' files, in GB; an unmeasured file adds nothing."""
        return max((self.peaks_mb.get(f, 0.0) for f in {file_of(n) for n in nodes}), default=0.0) / _MB_PER_GB

    def worker_gb(self, nodes: Iterable[str], *, prior_gb: float, safety: float) -> float:
        """The worst peak of these nodes' files times *safety*, never under *prior_gb*.

        ONE RULE FOR A SLURM TASK AND A BOX WORKER: each is one pytest process over some files. A measured
        file is its peak; an unmeasured one its :meth:`prior_mb` (siblings' median), raised to its ids' peak
        floors; with nothing measured at all, *prior_gb* alone stands.
        """
        peaks = self._file_peaks_mb(nodes).values()
        return task_gb(max(peaks, default=0.0) / _MB_PER_GB, prior_gb=prior_gb, safety=safety)


def memory_band(gb: float) -> int:
    """The memory class of an ask of *gb*: its power of two (1, 2, 4 ... GB), what :meth:`Measured.pack` groups by."""
    return math.ceil(math.log2(gb)) if gb > 1 else 0


def task_gb(peak_gb: float, *, prior_gb: float, safety: float) -> float:
    """What ONE Slurm task asks for a worker that peaked at *peak_gb*: the peak times *safety*, never under *prior_gb*.

    The one formula the plan's ``--mem`` and the claim's ``mem_gb`` demand both read, so a host never admits a
    part at one size and submits it at another (Ada 2026-10-16: admitted at a 13 GB run peak, submitted at
    352 GB a task against a 360 GB account ceiling with 232 GB of it held -- pending for an hour).
    """
    return max(peak_gb * safety, prior_gb)
