"""The JOB file a run is configured by, and the dataclasses every other layer reads instead of it.

TWO FILES, TWO OWNERS. The job file says WHAT runs -- entry, items, set-up, what one item costs, how
the work is cut -- and travels with the project. WHERE it may run (ssh target, Slurm account, this
box's share, candidate partitions and their QOS) belongs to the MACHINE, in
:mod:`lab_commons.hpc.grants`, because one job file is submitted from several boxes holding different
shares. Neither file repeats the other; the defaults below are the only other source of a value.

THE QUOTA IS READ, NOT TYPED. What the caller may use at once is the cluster's own answer (``sacctmgr``
association limits, read by :func:`lab_commons.hpc.slurm.probe`). A grant's share only LOWERS it, as a
:class:`Limits` built at plan time, because a plan built on a quota Slurm will not grant is a plan
that queues forever.

Example::

    [job]
    name = "sweep"
    workdir = "~/project"
    setup = ["source .venv/bin/activate"]
    entry = "project.module:evaluate"     # called once per item; must return JSON-serialisable data
    items = "items.json"                  # a JSON list, path relative to this file
    # with sha + repo_url (no workdir): native = "trained" runs the commit's declared trained (e.g. PGO) native
    # build, built in a Slurm job (lab_commons.hpc.artifact); the default "plain" is its release build

    [cost]                                # ONE item
    cpus = 1
    mem = "2 GiB"                         # Slurm memory
    wall = "90 s"

    [policy]                              # optional: how the work is cut
    longest_shard = "30 min"              # also shortest_shard, stall, poll: each a duration
    grants = ["ada-alice"]                # optional: only these grants (user or <cluster>-<user>) may run it

    [task]                                # optional: what ONE array task holds (default: one item, shared node)
    lane_domains = 2                      # a WHOLE node in lanes of 2 L3 domains, one item each (no [cost] cpus)
    # or: exclusive = true                # one item on the whole node (``--exclusive``), charged the whole node

A QUANTITY CARRIES ITS UNIT (user ruling 2026-11-25): ``mem``, ``wall`` and the policy's durations are quantity
strings read once by :func:`lab_commons.quantity.parse` into the unit each field computes in (GiB, seconds,
minutes); the unit-suffixed spellings (``mem_gb``, ``seconds``, ``shard_minutes_min``, ...) are refused with the
line that replaces them.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.dimensions import CPU, GPU, MEMORY
from lab_commons.hpc.builds import NATIVES, PLAIN
from lab_commons.quantity import Renamed, parse, refuse_renamed

#: Bytes per GiB -- a :class:`Cost` holds GiB (Slurm memory), :data:`~lab_commons.dimensions.MEMORY` counts bytes.
GIB: Final = 1024**3

#: The shortest ``status``/``gather --wait`` poll: each poll is an ssh to a login host (user ruling 2026-11-11).
POLL_FLOOR_S: Final = 60.0

__all__ = ['GIB', 'POLL_FLOOR_S', 'Cluster', 'Config', 'Cost', 'JobSpec', 'Limits', 'Policy', 'Task', 'load_config']


@dataclass(frozen=True)
class Cluster:
    """Which partitions a plan may ask for, their QOS and the nodes it avoids -- built from a grant (host, account)."""

    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)
    #: Nodes no task may use (``#SBATCH --exclude``), declared on the cluster (:mod:`lab_commons.hpc.grants`).
    exclude_nodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cost:
    """What ONE item needs. ``seconds`` is an estimate; :class:`Policy` carries the margin on it."""

    cpus: int = 1
    mem_gb: float = 1.0
    gpus: int = 0
    seconds: float = 60.0

    def __post_init__(self) -> None:
        """Refuse a cost no node could ever satisfy, at the point it is written."""
        if self.cpus < 1 or self.mem_gb <= 0 or self.gpus < 0 or self.seconds <= 0:
            msg = f'a per-item cost must be positive, got {self}'
            raise ValueError(msg)

    def demands(self) -> dict[str, float]:
        """The cost in :data:`~lab_commons.dimensions.DIMENSIONS` units (read by `width.fits`)."""
        return {CPU.name: self.cpus, MEMORY.name: self.mem_gb * GIB, GPU.name: self.gpus}


@dataclass(frozen=True)
class Limits:
    """Ceilings that LOWER the cluster's quota -- a grant's headroom. ``None`` means "whatever the cluster grants"."""

    cpus: int | None = None
    mem_gb: float | None = None
    gpus: int | None = None


@dataclass(frozen=True)
class Policy:
    """How the work is cut.

    A shard is one array task. It runs ``items`` back to back, so its length is the knob that trades
    per-task start-up (interpreter, imports) against how small a hole it can fill.
    """

    shard_minutes_min: float = 5.0
    shard_minutes_max: float = 60.0
    safety: float = 1.5
    max_array: int = 1000
    #: A task pending longer than this while a declared partition has a free slot is a STALL (``run gather``).
    stall_minutes: float = 30.0
    #: How often ``run gather --wait`` re-reads the cluster.
    poll_seconds: float = 120.0
    #: The most files one job's run directory may hold on the shared home, judged at gather (a file is an HPC
    #: resource: the home's quota counts files). A run is its manifest, its script and its one results file.
    file_budget: int = 10
    #: The grants this job may be placed on (``user`` or ``<cluster>-<user>``); empty: every reachable grant.
    grants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A window that is empty or inverted cannot place a single shard."""
        if (
            not 0 < self.shard_minutes_min <= self.shard_minutes_max
            or self.safety < 1
            or self.max_array < 1
            or self.stall_minutes <= 0
            or self.poll_seconds < POLL_FLOOR_S
            or self.file_budget < 1
        ):
            msg = f'an unusable policy: {self}'
            raise ValueError(msg)


@dataclass(frozen=True)
class Task:
    """What ONE array task holds: one item on a shared node, one item on a WHOLE node, or a whole node in lanes.

    ``exclusive`` asks for the node alone (``--exclusive``). Slurm charges such a task the WHOLE node against the
    account's quota, so the task is always planned at the node's CPUs, never less.

    ``lane_domains`` cuts a whole node (it implies ``exclusive``) into lanes of that many L3 domains
    and runs one item per lane at once. The
    shape is relative to the architecture: lanes per node, CPUs per lane and each lane's CPU ids and NUMA nodes
    are derived, per cluster, from the grant's entry in the cluster catalog (:mod:`lab_commons.hpc.topology`), so
    an item's CPUs come from there and ``[cost] cpus`` is not written; its memory and gpus stay per item.
    """

    exclusive: bool = False
    lane_domains: int | None = None

    def __post_init__(self) -> None:
        """A lane is at least one domain."""
        if self.lane_domains is not None and self.lane_domains < 1:
            msg = f'[task] lane_domains is at least 1, got {self.lane_domains}'
            raise ValueError(msg)

    @property
    def whole_node(self) -> bool:
        """The task holds its node alone: ``exclusive``, or cut into lanes."""
        return self.exclusive or self.lane_domains is not None


@dataclass(frozen=True)
class JobSpec:
    """What every array task executes, and where."""

    name: str = 'lab-hpc'
    workdir: str = '~'
    setup: tuple[str, ...] = ()
    entry: str = ''
    items: str = ''
    python: str = 'python'
    #: A commit to run at: each grant's home gets the commit's ONE artifact file (:mod:`lab_commons.hpc.artifact`).
    sha: str = ''
    #: Where that commit is fetched from (read-only HTTPS); required with ``sha``.
    repo_url: str = ''
    #: Every item reruns to the same result, so a short task may be moved (``[hpc.paging]``); declared, never guessed.
    rerun_safe: bool = False
    #: The artifact format a job at ``sha`` runs from (:data:`lab_commons.hpc.artifact.FORMATS`).
    artifact: str = 'tarball'
    #: The needs the job's environment serves (the tests' need vocabulary): their extras, and only theirs, are
    #: installed (:func:`lab_commons.extras.sync_command`) -- a job needing no heavy extra builds none.
    needs: tuple[str, ...] = ()
    #: Which native build the artifact carries (:data:`lab_commons.hpc.builds.NATIVES`): the declared plain release
    #: build, or the declared trained one (e.g. profile-guided), built in a Slurm job (:mod:`lab_commons.hpc.artifact`).
    native: str = PLAIN

    def __post_init__(self) -> None:
        """A commit names its remote; the native is one the kit builds."""
        if bool(self.sha) != bool(self.repo_url):
            msg = f'[job] sha and repo_url come together, got sha={self.sha!r} repo_url={self.repo_url!r}'
            raise ValueError(msg)
        if self.native not in NATIVES:
            msg = f'[job] native = {self.native!r} is not one of {list(NATIVES)}'
            raise ValueError(msg)


@dataclass(frozen=True)
class Config:
    """The whole configuration of one run, and the file it came from."""

    job: JobSpec = field(default_factory=JobSpec)
    cost: Cost = field(default_factory=Cost)
    policy: Policy = field(default_factory=Policy)
    task: Task = field(default_factory=Task)
    source: Path | None = None

    def items_path(self) -> Path:
        """The items file, resolved against the config file rather than the working directory."""
        path = Path(self.job.items).expanduser()
        if self.source is not None and not path.is_absolute():
            path = self.source.parent / path
        return path


_SECTIONS: dict[str, type] = {
    'job': JobSpec,
    'cost': Cost,
    'policy': Policy,
    'task': Task,
}


#: A section's QUANTITY keys: the declared key -> the field it fills, its dimension, and the base units (bytes,
#: seconds) in one unit of that field. The fields keep the units the planner computes in (GiB, s, min).
_QUANTITIES: Final[dict[str, dict[str, tuple[str, str, float]]]] = {
    'cost': {'mem': ('mem_gb', 'size', GIB), 'wall': ('seconds', 'duration', 1.0)},
    'policy': {
        'shortest_shard': ('shard_minutes_min', 'duration', 60.0),
        'longest_shard': ('shard_minutes_max', 'duration', 60.0),
        'stall': ('stall_minutes', 'duration', 60.0),
        'poll': ('poll_seconds', 'duration', 1.0),
    },
}
#: The unit-suffixed spellings the 2026-11-25 ruling retired, refused with their new line; never read.
_RENAMED: Final[dict[str, dict[str, Renamed]]] = {
    'cost': {'mem_gb': Renamed('mem', 'GiB'), 'seconds': Renamed('wall', 's')},
    'policy': {
        'shard_minutes_min': Renamed('shortest_shard', 'min'),
        'shard_minutes_max': Renamed('longest_shard', 'min'),
        'stall_minutes': Renamed('stall', 'min'),
        'poll_seconds': Renamed('poll', 's'),
    },
}


def _keys(name: str) -> set[str]:
    """The keys section *name* declares: its dataclass fields, each quantity field under its declared key."""
    quantities = _QUANTITIES.get(name, {})
    filled = {field_name for field_name, _, _ in quantities.values()}
    return (set(_SECTIONS[name].__dataclass_fields__) - filled) | set(quantities)


def _section(name: str, raw: dict[str, Any]) -> Any:  # noqa: ANN401 -- one of the section types
    """Build one section, refusing a key it does not declare -- a typo is not a default -- or a retired one."""
    refuse_renamed(f'[{name}]', raw, _RENAMED.get(name, {}))
    known = _keys(name)
    unknown = set(raw) - known
    if unknown:
        msg = f'[{name}] has unknown keys {sorted(unknown)}; known: {sorted(known)}'
        raise ValueError(msg)
    quantities = _QUANTITIES.get(name, {})
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in quantities:
            filled, dimension, per = quantities[key]
            values[filled] = parse(f'[{name}] {key}', value, dimension, positive=True) / per
        else:
            values[key] = tuple(value) if isinstance(value, list) else value
    return _SECTIONS[name](**values)


def load_config(path: Path | str) -> Config:
    """Read *path* into a :class:`Config`. Every section is optional; an unknown section or key is refused."""
    source = Path(path).expanduser().resolve()
    raw = tomllib.loads(source.read_text(encoding='utf-8'))
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        msg = f'{source}: unknown sections {sorted(unknown)}; known: {sorted(_SECTIONS)}'
        raise ValueError(msg)
    sections = {name: _section(name, raw.get(name, {})) for name in _SECTIONS}
    if sections['task'].lane_domains is not None and 'cpus' in raw.get('cost', {}):
        msg = f'{source}: [task] lane_domains derives each item CPUs from the cluster catalog; drop [cost] cpus'
        raise ValueError(msg)
    return Config(**sections, source=source)
