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

    [cost]                                # ONE item
    cpus = 1
    mem_gb = 2
    seconds = 90

    [policy]                              # optional: how the work is cut
    shard_minutes_max = 30
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
    """Where commands run and which partitions a plan may choose from -- built from a grant, not a job file."""

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

    job: JobSpec = field(default_factory=JobSpec)
    cost: Cost = field(default_factory=Cost)
    policy: Policy = field(default_factory=Policy)
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
}


def _section(name: str, raw: dict[str, Any]) -> Any:  # noqa: ANN401 -- one of the three section types
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
