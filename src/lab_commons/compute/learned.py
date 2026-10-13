"""THE ONE MEASURED DEMAND TABLE: what a repository's finished runs measured, carried to its next admission.

ONE FILE PER REPOSITORY, ``<config dir>/compute-learned/<key>.json`` (beside the machine file), keyed by
the remote URL and machine-local, so it serves every host and grant the machine holds. It holds two
kinds of fact, both written by finished runs -- a box run (:mod:`lab_commons.dev.forgeproduce`) and a
Slurm run (:mod:`lab_commons.hpc.run`) alike -- and never by hand:

* PER TEST (:class:`Measured`) -- what prices a shard and a worker: seconds per node id, floors, per-file
  overheads and per-file peak RSS. :meth:`Measured.pack` cuts a Slurm array from it;
  :meth:`Measured.worker_gb` is the memory one worker needs -- a Slurm task's request and the input
  :func:`lab_commons.dev.bounded.worker_width` divides a box's memory by.
* PER JOB (:class:`Run`), keyed by ``(producer, need-set)`` -- the job's peak memory, cpu-seconds and wall
  seconds, read back as a :class:`~lab_commons.compute.model.Demand` by :func:`demand`, with a declared
  prior for a job never measured.

WHY IT IS NOT A FLAG. Measured 2026-10-11: the submit flag naming a previous record was optional and no
caller passed it, so every downstream run planned round 0 blind -- 4441 of 4441 files priced at ``[cost]``
and 1 GB, 26 of 93 shards OUT_OF_MEMORY, two retry rounds that took 3.0 of the run's 4.2 hours.

PER TEST, EACH FACT KEYED BY WHAT IT DESCRIBES. ``durations`` -- seconds per node id, the sum of its
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

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lab_commons.compute.model import Demand
from lab_commons.config import config_path

if TYPE_CHECKING:
    from lab_commons.hpc.config import Cost, Policy

__all__ = [
    'CLUSTER_PRIOR',
    'LOCAL',
    'OOM_GROWTH',
    'SLURM',
    'Measured',
    'Run',
    'demand',
    'directory',
    'learn',
    'learn_peaks',
    'learned',
    'learned_demand',
    'producer_of',
    'record_run',
    'run_of',
    'worker_gb',
]

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
        """One Slurm task's memory: :meth:`worker_gb` over these items' files, floored at ``cost.mem_gb``."""
        return self.worker_gb([n for ids in items for n in ids], prior_gb=cost.mem_gb, safety=policy.safety)

    def worker_gb(self, nodes: Iterable[str], *, prior_gb: float, safety: float) -> float:
        """The worst measured peak of these nodes' files times *safety*, never under *prior_gb*.

        ONE RULE FOR A SLURM TASK AND A BOX WORKER: each is one pytest process over some files. An
        unmeasured file is priced at *prior_gb* alone, so it cannot mis-price the rest.
        """
        files = {_file(n) for n in nodes}
        worst = max((self.peaks_mb.get(f, 0.0) for f in files), default=0.0) * safety / _MB_PER_GB
        return max(worst, prior_gb)


# --- per job: (producer, need-set) -> Run --------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """What one finished job measured: peak memory of its whole process tree, cpu-seconds, wall seconds."""

    peak_mem_gb: float
    cpu_s: float
    wall_s: float

    def __post_init__(self) -> None:
        """Refuse a measurement no run could have produced."""
        if self.peak_mem_gb < 0 or self.cpu_s < 0 or self.wall_s <= 0:
            msg = f'a run measures a non-negative peak and cpu and a positive wall, got {self}'
            raise ValueError(msg)

    @property
    def cpu_hours(self) -> float:
        """Cpu-seconds in hours -- the cost :func:`lab_commons.compute.model.rank` divides value by."""
        return self.cpu_s / 3600

    def demand(self, needs: Iterable[str] = ()) -> Demand:
        """The vector admission compares: the cores it kept busy, its peak memory, one of each named need."""
        cores = max(1, math.ceil(self.cpu_s / self.wall_s - 1e-9))
        return Demand({'cpu': cores, 'mem_gb': self.peak_mem_gb, **dict.fromkeys(needs, 1.0)})


def _key(producer: str, needs: Iterable[str]) -> str:
    return f'{producer}|{",".join(sorted(needs))}'


def directory() -> Path:
    """``compute-learned/`` beside the machine config (:func:`lab_commons.config.config_path`)."""
    return config_path().parent / 'compute-learned'


def _path(repo: str) -> Path:
    return directory() / f'{hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]}.json'


def learned(repo: str) -> dict[str, Any]:
    """Everything learned for *repo* (the per-test keys of :class:`Measured`, and ``runs``); none yet is ``{}``."""
    path = _path(repo)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _write(repo: str, table: dict[str, Any]) -> Path:
    target = _path(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**table, 'repo': repo}), encoding='utf-8')
    return target


def learn(record: dict[str, Any]) -> Path:
    """Keep *record*'s per-test measurements as its repository's latest, keeping its per-job runs."""
    return _write(record['repo'], {**learned(record['repo']), **Measured.of(record).record()})


def learn_peaks(repo: str, peaks_mb: Mapping[str, float]) -> Path:
    """Fold per-FILE peaks a box run measured into *repo*'s table; a newer peak replaces an older one."""
    table = learned(repo)
    return _write(repo, {**table, 'peaks_mb': {**table.get('peaks_mb', {}), **peaks_mb}})


def record_run(repo: str, producer: str, needs: Iterable[str], run: Run, *, killed: bool = False) -> Path:
    """Keep what one finished job of *repo* measured under ``(producer, needs)``.

    A *killed* run's peak is the limit it died at, not its need: it is grown by :data:`OOM_GROWTH` and
    never lowers a peak already known (the module docstring).
    """
    table = learned(repo)
    runs = dict(table.get('runs', {}))
    key = _key(producer, needs)
    if killed:
        known = float(runs.get(key, {}).get('peak_mem_gb', 0.0))
        run = Run(max(known, run.peak_mem_gb * OOM_GROWTH), run.cpu_s, run.wall_s)
    runs[key] = asdict(run)
    return _write(repo, {**table, 'runs': runs})


def run_of(repo: str, producer: str, needs: Iterable[str]) -> Run | None:
    """The last run of ``(producer, needs)`` measured for *repo*, or ``None``."""
    found = learned(repo).get('runs', {}).get(_key(producer, needs))
    return None if found is None else Run(**found)


def demand(repo: str, producer: str, needs: Iterable[str], *, prior: Run) -> Demand:
    """The :class:`~lab_commons.compute.model.Demand` of ``(producer, needs)``: measured, else *prior*."""
    named = frozenset(needs)
    return (run_of(repo, producer, named) or prior).demand(named)


def worker_gb(repo: str, files: Iterable[str], *, prior_gb: float, safety: float) -> float:
    """GB one worker running *files* needs, from *repo*'s per-file peaks (:meth:`Measured.worker_gb`)."""
    return Measured.of(learned(repo)).worker_gb(files, prior_gb=prior_gb, safety=safety)


#: What an unmeasured cluster run is taken to hold: one core (the run sizes its array to what is free).
CLUSTER_PRIOR: Final = Run(peak_mem_gb=0.0, cpu_s=1.0, wall_s=1.0)

#: The producer a box part runs: the repository's declared ``produce`` in a tree at the sha.
LOCAL: Final = 'local'

#: The producer a cluster part runs: a Slurm array (:mod:`lab_commons.hpc.run`).
SLURM: Final = 'slurm'


def producer_of(*, cluster: bool) -> str:
    """The producer key a part's runs are learned under: :data:`SLURM` on a cluster, else :data:`LOCAL`."""
    return SLURM if cluster else LOCAL


def learned_demand(repo_url: str, *, cluster: bool, cores: int) -> Callable[[frozenset[str]], Demand]:
    """``needs -> Demand``: what the last run of ``(this producer, that need-set)`` measured (:func:`run_of`).

    A part IS its need-set (:mod:`lab_commons.hpc.needsets`), so the need-set is the key; the producer is
    :func:`producer_of`. Unmeasured, a cluster part is :data:`CLUSTER_PRIOR` and a box part the whole box
    (*cores*): an unmeasured box run spreads over every core it is given.
    """
    producer = producer_of(cluster=cluster)
    prior = CLUSTER_PRIOR if cluster else Run(peak_mem_gb=0.0, cpu_s=float(cores), wall_s=1.0)
    return lambda needs: (run_of(repo_url, producer, needs) or prior).demand(needs)
