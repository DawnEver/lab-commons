"""The one file a run is configured by, and the dataclasses every other layer reads instead of it.

ONE SOURCE, EVERY NUMBER. Where to connect, which partitions are candidates, what one item costs, how
long a piece of work should be, how the job's environment is set up -- all of it is in one TOML file,
and the defaults below are the only other place a value can come from. A layer that needs a number
takes it from these dataclasses; none of them reads the file or carries a private default.

THE QUOTA IS READ, NOT TYPED. What the caller may use at once is the cluster's own answer (``sacctmgr``
association limits, read by :func:`lab_commons.hpc.slurm.probe`). ``[limits]`` exists only to LOWER it
-- a share agreed with a group, say -- and a value above the cluster's is clipped to the cluster's,
because a plan built on a quota Slurm will not grant is a plan that queues forever.

Example::

    [cluster]
    host = "user@login.example.ac.uk"     # empty: run commands locally (already on a login node)
    partitions = ["devq", "shortq", "defq"]  # candidates, in preference order
    qos = { devq = "dev" }                # partitions that need a --qos to be used at all

    [job]
    name = "sweep"
    workdir = "~/project"
    setup = ["source .venv/bin/activate"]
    entry = "project.module:evaluate"     # called once per item; must return JSON-serialisable data
    items = "items.json"                  # a JSON list, path relative to this file

    [cost]                                # ONE item
    cpus = 1
    mem_gb = 2
    seconds = 90
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.resources import CPU, GPU, MEMORY

#: Bytes per GiB -- the config speaks GB for humans, :data:`~lab_commons.resources.MEMORY` counts bytes.
GIB: Final = 1024**3

__all__ = ['GIB', 'Cluster', 'Config', 'Cost', 'JobSpec', 'Limits', 'Policy', 'load_config']


@dataclass(frozen=True)
class Cluster:
    """Where commands run and which partitions a plan may choose from."""

    host: str = ''
    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)
    account: str = ''


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
        """The cost in :data:`lab_commons.resources.DIMENSIONS` units -- what ``fits`` and ``Broker.admit`` read."""
        return {CPU.name: self.cpus, MEMORY.name: self.mem_gb * GIB, GPU.name: self.gpus}


@dataclass(frozen=True)
class Limits:
    """Ceilings that LOWER the cluster's quota. ``None`` means "whatever the cluster grants"."""

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

    def __post_init__(self) -> None:
        """A window that is empty or inverted cannot place a single shard."""
        if not 0 < self.shard_minutes_min <= self.shard_minutes_max or self.safety < 1 or self.max_array < 1:
            msg = f'an unusable policy: {self}'
            raise ValueError(msg)


@dataclass(frozen=True)
class JobSpec:
    """What every array task executes, and where."""

    name: str = 'lab-hpc'
    workdir: str = '~'
    setup: tuple[str, ...] = ()
    entry: str = ''
    items: str = ''
    python: str = 'python'


@dataclass(frozen=True)
class Config:
    """The whole configuration of one run, and the file it came from."""

    cluster: Cluster = field(default_factory=Cluster)
    job: JobSpec = field(default_factory=JobSpec)
    cost: Cost = field(default_factory=Cost)
    limits: Limits = field(default_factory=Limits)
    policy: Policy = field(default_factory=Policy)
    source: Path | None = None

    def items_path(self) -> Path:
        """The items file, resolved against the config file rather than the working directory."""
        path = Path(self.job.items).expanduser()
        if self.source is not None and not path.is_absolute():
            path = self.source.parent / path
        return path


_SECTIONS: dict[str, type] = {
    'cluster': Cluster,
    'job': JobSpec,
    'cost': Cost,
    'limits': Limits,
    'policy': Policy,
}


def _section(name: str, raw: dict[str, Any]) -> Any:  # noqa: ANN401 -- one of the five section types
    """Build one section, refusing a key the dataclass does not declare -- a typo is not a default."""
    kind = _SECTIONS[name]
    known = set(kind.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        msg = f'[{name}] has unknown keys {sorted(unknown)}; known: {sorted(known)}'
        raise ValueError(msg)
    values = {key: tuple(value) if isinstance(value, list) else value for key, value in raw.items()}
    return kind(**values)


def load_config(path: Path | str) -> Config:
    """Read *path* into a :class:`Config`. Every section is optional; an unknown section or key is refused."""
    source = Path(path).expanduser().resolve()
    raw = tomllib.loads(source.read_text(encoding='utf-8'))
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        msg = f'{source}: unknown sections {sorted(unknown)}; known: {sorted(_SECTIONS)}'
        raise ValueError(msg)
    sections = {name: _section(name, raw.get(name, {})) for name in _SECTIONS}
    return Config(**sections, source=source)
