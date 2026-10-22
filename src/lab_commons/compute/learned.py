"""THE ONE MEASURED DEMAND TABLE: what a repository's finished runs measured, carried to its next admission.

ONE FILE PER REPOSITORY, ``<config dir>/compute-learned/<key>.json`` (beside the machine file), keyed by
the remote URL and machine-local, so it serves every host and grant the machine holds. It holds two
kinds of fact, both written by finished runs -- a box run (:mod:`dev_lab.dev.forgeproduce`) and a
Slurm run (:mod:`lab_commons.hpc.run`) alike -- and never by hand:

* PER TEST (:class:`Measured`) -- what prices a shard and a worker: seconds per node id, floors, per-file
  overheads and per-file peak RSS. :meth:`Measured.pack` cuts a Slurm array from it, grouping files by memory class;
  :meth:`Measured.item_gb` is one item's memory;
  :meth:`Measured.worker_gb` is the memory one worker needs -- a Slurm task's request and a box
  selection's claim (:func:`dev_lab.dev.verifybox.selection_mem_gb`).
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

A PEAK READ AT A KILL IS THE LIMIT, NOT THE NEED, AND IT IS NO FILE'S PEAK. Measured 2026-10-13 (Ada):
children OOM-killed at 1 GB read ~1120 MB. A signalled ONE-FILE item keeps that read as its file's FLOOR,
ungrown -- the next run asks it times the safety, more than the limit it died at -- and never lowers a peak
already known. The retry's growth is the run's own ``oom`` multiplier, applied ONCE. Measured 2026-10-16
(Ada): the read was also grown by :data:`OOM_GROWTH` and the retry grew it again, so a 20 GB kill was
re-asked at 120 GB, killed, and taught 240 GB to each of 56 files of ONE multi-file item; every later run
of the part asked 352 GB a task. A signalled MULTI-file item teaches no peak at all: which file held the
memory is unknown, and its retry is bisected (:mod:`lab_commons.hpc.run`) until one file says.
The declared ``cost.mem_gb`` is a floor too: the peak is the pytest child's, and the
task's cgroup also holds the item runner (Ada 2026-10-12: a 150 MB peak priced 225M and 429M, OOM both).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.compute.model import Demand
from lab_commons.config import config_path
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.needsets import needs_of

__all__ = [
    'BOX_HEADROOM',
    'BOX_WALL_S',
    'CLUSTER_PRIOR',
    'LOCAL',
    'OOM_GROWTH',
    'PART_FILES',
    'PART_WIDTH',
    'SLURM',
    'Measured',
    'Run',
    'box_demand',
    'cluster_demand',
    'demand',
    'directory',
    'fit_width',
    'folded',
    'learn',
    'learn_peaks',
    'learn_record',
    'learned',
    'memory_band',
    'part_files',
    'record_ask',
    'record_run',
    'request_demand',
    'run_of',
    'selection_gb',
    'task_gb',
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
        recorded for every file, an upper bound for the file alone; a SIGNALLED item's peak is a floor of
        its file when it holds ONE, and nobody's when it holds several (the module docstring).
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
        if peak is None or (signalled and len(files) > 1):
            return
        for name in files:
            floor = self.peaks_mb.get(name, 0.0) if signalled else 0.0
            self.peaks_mb[name] = max(floor, float(peak))

    def killed(self, ids: Sequence[str], folded: dict[str, Any], spent: float) -> None:
        """An item killed after *spent* seconds: its first unfinished id ran for what the rest does not explain.

        Pytest runs an item's ids in the order given, so the first id without a cell is the one the
        kill interrupted. It took at least *spent* minus the finished ids' durations and the files' known
        overheads; that is its FLOOR. The ids after it never started and keep their estimate.
        """
        rest = [n for n in ids if n not in folded['cells']]
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
        array's memory was its heaviest file's.
        """
        by = self._by_file()
        ordered = sorted(files, key=band) if band is not None else list(files)
        items: list[list[str]] = []
        last = None
        for ids in ordered:
            key = band(ids) if band is not None else None
            if items and key == last and self._seconds([*items[-1], *ids], cost, by)[0] <= budget:
                items[-1] = [*items[-1], *ids]
            else:
                items.append(list(ids))
            last = key
        return items

    def item_gb(
        self, ids: Iterable[str], cost: Cost, policy: Policy, grown: Mapping[str, float] | None = None
    ) -> float:
        """ONE ITEM's memory: each of its files' :meth:`worker_gb` times its OOM growth in this run, the worst.

        *grown* is the multiplier a run applied to a file whose item was OOM-killed (:mod:`lab_commons.hpc.run`);
        a file absent from it asks its learned peak times the safety, never under ``cost.mem_gb`` -- never a
        repo-wide constant, never an inherited maximum.
        """
        growth = grown or {}
        files = {_file(n) for n in ids}
        return max(
            (self.worker_gb([f], prior_gb=cost.mem_gb, safety=policy.safety) * growth.get(f, 1.0) for f in files),
            default=cost.mem_gb,
        )

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
        return max((self.peaks_mb.get(f, 0.0) for f in {_file(n) for n in nodes}), default=0.0) / _MB_PER_GB

    def worker_gb(self, nodes: Iterable[str], *, prior_gb: float, safety: float) -> float:
        """The worst peak of these nodes' files times *safety*, never under *prior_gb*.

        ONE RULE FOR A SLURM TASK AND A BOX WORKER: each is one pytest process over some files. A measured
        file is its peak; an unmeasured one its :meth:`prior_mb` (siblings' median); with nothing measured
        at all, *prior_gb* alone stands.
        """
        files = {_file(n) for n in nodes}
        peaks = [self.peaks_mb[f] if f in self.peaks_mb else (self.prior_mb(f) or 0.0) for f in files]
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


# --- per job: (producer, need-set) -> Run --------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """What one finished job measured: peak memory of its whole process tree, cpu-seconds, wall seconds."""

    peak_mem_gb: float
    cpu_s: float
    wall_s: float
    files: float = 0.0  # what a cluster run left in the home (tree, run, env); 0 is unmeasured

    def __post_init__(self) -> None:
        """Refuse a measurement no run could have produced."""
        if self.peak_mem_gb < 0 or self.cpu_s < 0 or self.wall_s <= 0 or self.files < 0:
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


def folded(repo: str) -> frozenset[str]:
    """The run records (by attachment name) already folded into *repo*'s table: the cache's index."""
    return frozenset(learned(repo).get('folded', ()))


def learn_record(repo: str, name: str, record: Mapping[str, Any]) -> Path:
    """Fold one forge run record *name* into *repo*'s table: each per-test fact it carries replaces the older one.

    THE TABLE IS A CACHE OF THE FORGE (D4, 2026-10-16): every host folds the same attached records, so every
    host prices from the same numbers; *name* is remembered so a record is downloaded and folded once.
    """
    table = learned(repo)
    merged = {key: {**table.get(key, {}), **facts} for key, facts in Measured.of(dict(record)).record().items()}
    parts = dict(table.get(PART_FILES, {}))
    files = sorted({_file(n) for n in (record.get('durations') or {})} | set(record.get('peaks_mb') or {}))
    if files and record.get('platform'):
        parts[_key(LOCAL, needs_of(str(record['platform'])))] = files
    folded_now = sorted({*table.get('folded', ()), name})
    return _write(repo, {**table, **merged, PART_FILES: parts, 'folded': folded_now})


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
        run = Run(max(known, run.peak_mem_gb * OOM_GROWTH), run.cpu_s, run.wall_s, run.files)
    runs[key] = asdict(run)
    return _write(repo, {**table, 'runs': runs})


def record_ask(repo: str, producer: str, needs: Iterable[str], ask_gb: float, *, safety: float) -> Path:
    """Raise ``(producer, needs)``'s known peak so its next demand asks at least *ask_gb* a task (:func:`task_gb`).

    A plan that could not be placed, or a job Slurm held for a resource limit, KNOWS the ask the measured run
    peak understated; kept, this host stops admitting the part at the old size. Never lowers a known peak.
    """
    table = learned(repo)
    runs = dict(table.get('runs', {}))
    key = _key(producer, needs)
    known = Run(**runs[key]) if key in runs else CLUSTER_PRIOR
    runs[key] = asdict(Run(max(known.peak_mem_gb, ask_gb / safety), known.cpu_s, known.wall_s, known.files))
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


#: What an unmeasured cluster run is taken to hold: one core (the run sizes its array to what is free), and
#: the files it leaves in the home -- CONSERVATIVE: Ada 2026-10-14 held ~0.3 M files of forge trees, ~0.16 M
#: of 48 envs and ~0.18 M of collect, so one run's tree, run and env are priced high until measured.
CLUSTER_PRIOR: Final = Run(peak_mem_gb=0.0, cpu_s=1.0, wall_s=1.0, files=50_000.0)

#: The producer a box part runs: the repository's declared ``produce`` in a tree at the sha.
LOCAL: Final = 'local'

#: The producer a cluster part runs: a Slurm array (:mod:`lab_commons.hpc.run`).
SLURM: Final = 'slurm'

#: How far a box part's declared memory sits above the peak it measured: the box kills the tree AT the declaration.
BOX_HEADROOM: Final = 1.5

#: The table key of each need-set's test FILES, as its latest forge record ran them (:func:`learn_record`).
PART_FILES: Final = 'part_files'

#: The workers an UNMEASURED box part runs at most: its declared cores, and the pytest ``-n`` it then runs.
PART_WIDTH: Final = 4

#: The wall a box part declares, in seconds: past it the box kills the tree (one day, as a hung run).
BOX_WALL_S: Final = 24 * 3600


def cluster_demand(
    repo_url: str, *, cost: Cost | None = None, policy: Policy | None = None
) -> Callable[[frozenset[str]], Demand]:
    """``needs -> Demand`` a cluster part asks: what the last ``(slurm, need-set)`` run measured, else the prior.

    Its ``mem_gb`` is ONE TASK's ask -- :func:`task_gb` of the run's peak under *cost* and *policy* (the forge's
    defaults when omitted), the size the plan submits -- so an account whose headroom cannot hold one task of the
    part never claims it.

    A part IS its need-set (:mod:`lab_commons.hpc.needsets`), so the need-set is the key. It also asks the
    ``files`` it leaves in the home (measured, else :data:`CLUSTER_PRIOR`'s), against the account's file headroom.
    """
    item, cut = cost or Cost(), policy or Policy()

    def ask(needs: frozenset[str]) -> Demand:
        run = run_of(repo_url, SLURM, needs) or CLUSTER_PRIOR
        memory = task_gb(run.peak_mem_gb, prior_gb=item.mem_gb, safety=cut.safety)
        return Demand({**run.demand(needs), 'mem_gb': memory, 'files': run.files or CLUSTER_PRIOR.files})

    return ask


def part_files(repo_url: str, needs: Iterable[str]) -> tuple[str, ...]:
    """The test files the need-set's part ran in its latest forge record (:func:`learn_record`); ``()`` if none."""
    return tuple(learned(repo_url).get(PART_FILES, {}).get(_key(LOCAL, needs), ()))


def selection_gb(repo: str, files: Iterable[str], workers: int, *, whole_gb: float = 0.0) -> float:
    """THE ONE SELECTION ARITHMETIC: the memory a tree of *workers* pytest workers over *files* declares.

    Each worker at :func:`worker_gb` -- the worst file's learned peak times :data:`BOX_HEADROOM`, an unmeasured
    file at its siblings' median, never under :class:`~lab_commons.hpc.config.Cost`'s per-task floor (the
    runner a worker carries) -- and never under a measured *whole_gb* tree peak times the headroom. A verify
    selection (:func:`dev_lab.dev.verifybox.selection_mem_gb`) and an unmeasured box part
    (:func:`box_demand`) both read it, so a part of ONE cheap test asks one floor, not a repo-wide constant.
    """
    per = worker_gb(repo, files, prior_gb=Cost().mem_gb, safety=BOX_HEADROOM)
    return round(max(max(1, workers) * per, whole_gb * BOX_HEADROOM), 3)


def fit_width(cap: int, *, cpu: float, mem_gb: float, per_gb: float) -> int:
    """THE ONE WIDTH RULE: *cap* workers, narrowed to the FREE *cpu* and *mem_gb* at *per_gb* each, never under one.

    One worker that does not fit is still one: its claim waits for the room, never priced under a worker. A
    box part (:func:`box_demand`) and a verify selection (:func:`dev_lab.dev.verifybox.selection_workers`)
    both read it, and the pytest ``-n`` each runs IS this number (declared == used).
    """
    held = math.floor(mem_gb / per_gb) if per_gb > 0 else cap
    return max(1, min(cap, math.floor(cpu), held))


def box_demand(repo_url: str, needs: frozenset[str], *, cpu: float, mem_gb: float, ids: int | None = None) -> Demand:
    """What a box part DECLARES to the box -- admitted at it, and killed past it (:func:`lab_commons.box.run`).

    *cpu* and *mem_gb* are the box's FREE room now. One worker's price: measured, its last ``(local, need-set)``
    run's whole-tree peak times :data:`BOX_HEADROOM` over the cores it kept busy; unmeasured, its SELECTION's
    (:func:`selection_gb` of the files its need-set's latest forge record ran, :func:`part_files`). Its width is
    :func:`fit_width` of the cores it kept busy, else one per id it is handed (*ids*, as the commit's posted
    parts count them; ``None`` unknown) up to :data:`PART_WIDTH`. The cores ARE the workers its producer runs
    (:func:`dev_lab.dev.verifybox.claimed_width`) and the memory is that width at the worker's price, so a
    part on a memory-starved box runs narrower instead of never being admitted. One seat of each need.
    """
    run = run_of(repo_url, LOCAL, needs)
    if run is None:
        cap = PART_WIDTH if ids is None else max(1, min(PART_WIDTH, ids))
        per = selection_gb(repo_url, part_files(repo_url, needs), 1)
    else:
        cap = max(1, int(run.demand(needs)['cpu']))
        per = run.peak_mem_gb * BOX_HEADROOM / cap
    cores = fit_width(cap, cpu=cpu, mem_gb=mem_gb, per_gb=per)
    return Demand({**dict.fromkeys(needs, 1.0), 'cpu': cores, 'mem_gb': max(round(cores * per, 3), 1.0)})


def request_demand(repo_url: str, producer: str, needs: frozenset[str], *, cpu: float, mem_gb: float) -> Demand:
    """What a REQUEST declares to the box: its last run of ``(producer, needs)``, else the *cpu*/*mem_gb* its row sizes.

    Keyed by the producer's NAME, so a request never shares a test part's record of the same need-set; measured,
    the cores it kept busy and its peak times :data:`BOX_HEADROOM`. One seat of each need.
    """
    run = run_of(repo_url, producer, needs)
    if run is not None:
        cpu, mem_gb = max(1, int(run.demand(needs)['cpu'])), max(1.0, run.peak_mem_gb * BOX_HEADROOM)
    return Demand({**dict.fromkeys(needs, 1.0), 'cpu': cpu, 'mem_gb': mem_gb})
