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

See :mod:`lab_commons.compute.measured` for how the per-test facts are kept and read.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from lab_commons.compute.durable import durable_text, read_table
from lab_commons.compute.measured import (
    ACCUMULATED_CLASSES,
    TIMEOUT_GROWTH,
    Measured,
    file_of,
    memory_band,
    task_gb,
)
from lab_commons.compute.model import Demand
from lab_commons.config import config_path
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.needsets import needs_of

__all__ = [
    'ACCUMULATED_CLASSES',
    'BOX_HEADROOM',
    'BOX_WALL_S',
    'CLUSTER_PRIOR',
    'LOCAL',
    'OOM_GROWTH',
    'PART_FILES',
    'PART_WIDTH',
    'SLURM',
    'TIMEOUT_GROWTH',
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

#: How much more memory a run asks after an OUT_OF_MEMORY -- the next round's request and a killed peak's floor.
OOM_GROWTH: Final = 2

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


READ_RETRY_S: Final = 0.5
"""How long a reader waits out a table it cannot parse before reading it as nothing learned (seconds)."""


def learned(repo: str) -> dict[str, Any]:
    """Everything learned for *repo* (the per-test keys of :class:`Measured`, and ``runs``); none yet is ``{}``.

    The table is ADVISORY: an unparseable one (a writer from before whole-file writes, a torn disk) is
    retried for :data:`READ_RETRY_S`, then quarantined and read as ``{}`` -- the caller's prior -- never raised
    through a run (:func:`~lab_commons.compute.durable.read_table`).
    """
    path = _path(repo)
    deadline = time.monotonic() + READ_RETRY_S
    while True:
        try:
            return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        except (ValueError, OSError):
            if time.monotonic() >= deadline:
                return read_table(path)
            time.sleep(0.05)


def _write(repo: str, table: dict[str, Any]) -> Path:
    """Write *repo*'s table WHOLE (temp, fsync, rename): a concurrent reader sees the old file or the new one."""
    return durable_text(_path(repo), json.dumps({**table, 'repo': repo}))


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
    files = sorted({file_of(n) for n in (record.get('durations') or {})} | set(record.get('peaks_mb') or {}))
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
    ``files`` it leaves in the home (measured, else :data:`CLUSTER_PRIOR`'s), against the account's file headroom,
    and the one ``submits`` its run takes of the account's QOS.
    """
    item, cut = cost or Cost(), policy or Policy()

    def ask(needs: frozenset[str]) -> Demand:
        run = run_of(repo_url, SLURM, needs) or CLUSTER_PRIOR
        memory = task_gb(run.peak_mem_gb, prior_gb=item.mem_gb, safety=cut.safety)
        return Demand({**run.demand(needs), 'mem_gb': memory, 'files': run.files or CLUSTER_PRIOR.files, 'submits': 1})

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
