"""Which cluster accounts THIS MACHINE may use, and how much of each -- one per-machine file.

A GRANT IS A SHARE OF AN ACCOUNT, HELD BY A BOX. One workstation may hold several grants (two clusters,
two Slurm accounts); one login may be shared by several workstations, each holding its own share of it.
The file says only what is agreed -- the share; what each box is USING is read from Slurm at plan time,
never stored, because every job a box submits carries ``--comment=lc:ws=<workstation>`` and ``squeue``
reports it back (:mod:`lab_commons.hpc.slurm`). A stored usage figure would be a second copy of Slurm's
own state, wrong the moment a job ends.

NO SECRETS. ``account`` is the ``ssh`` target; keys, ports and jump hosts stay in ``~/.ssh/config``.

Example (``~/.config/lab-commons/hpc.toml``, or the path in ``$LAB_COMMONS_HPC_GRANTS``)::

    workstation = "lab-ws-07"                     # stable, unique among the boxes sharing an account

    [[grant]]
    account = "user@login.cluster.example"        # ssh target
    slurm_account = "acct-free"
    cpus = 64                                     # this box's share of the account's CPU quota
    priority = 0                                  # optional; breaks a tie between equally fast grants
    partitions = ["shortq", "defq"]               # optional cluster facts: candidates, in preference order
    qos = { devq = "dev" }                        # optional: partitions that need a --qos
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.config import Cluster

__all__ = ['COMMENT_PREFIX', 'GRANTS_ENV', 'Grant', 'Machine', 'grants_path', 'load_grants']

#: Environment override of the per-machine grants file.
GRANTS_ENV: Final = 'LAB_COMMONS_HPC_GRANTS'

#: Every submitted job's ``--comment`` starts with this, followed by the workstation name.
COMMENT_PREFIX: Final = 'lc:ws='


@dataclass(frozen=True)
class Grant:
    """One share of one Slurm account, reached over ``ssh`` at ``account``."""

    account: str
    slurm_account: str
    cpus: int
    priority: int = 0
    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """A share that cannot hold one CPU is not a share."""
        if not self.account or not self.slurm_account:
            msg = f'a grant names both its ssh account and its slurm_account, got {self}'
            raise ValueError(msg)
        if self.cpus < 1:
            msg = f'grant {self.account} ({self.slurm_account}): a share must be positive, got cpus={self.cpus}'
            raise ValueError(msg)

    def cluster(self) -> Cluster:
        """The planner's view of this grant: where to connect and what to ask for."""
        return Cluster(host=self.account, partitions=self.partitions, qos=self.qos, account=self.slurm_account)


@dataclass(frozen=True)
class Machine:
    """A workstation's name and every grant it holds."""

    workstation: str
    grants: tuple[Grant, ...]

    @property
    def comment(self) -> str:
        """The ``--comment`` every job of this machine carries."""
        return COMMENT_PREFIX + self.workstation


def grants_path() -> Path:
    """``$LAB_COMMONS_HPC_GRANTS``, else ``~/.config/lab-commons/hpc.toml``."""
    override = os.environ.get(GRANTS_ENV)
    return Path(override).expanduser() if override else Path.home() / '.config' / 'lab-commons' / 'hpc.toml'


_GRANT_KEYS: Final = frozenset(Grant.__dataclass_fields__)


def _grant(raw: dict[str, Any], source: Path) -> Grant:
    unknown = set(raw) - _GRANT_KEYS
    if unknown:
        msg = f'{source}: [[grant]] has unknown keys {sorted(unknown)}; known: {sorted(_GRANT_KEYS)}'
        raise ValueError(msg)
    return Grant(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})


def load_grants(path: Path | None = None) -> Machine:
    """Read the per-machine grants file. A missing file, a duplicate account or a bad share is refused by name."""
    source = (path or grants_path()).expanduser()
    if not source.is_file():
        msg = f'no HPC grants file at {source}: write one (see lab_commons.hpc.grants) or point ${GRANTS_ENV} at it'
        raise FileNotFoundError(msg)
    raw = tomllib.loads(source.read_text(encoding='utf-8'))
    unknown = set(raw) - {'workstation', 'grant'}
    if unknown:
        msg = f'{source}: unknown keys {sorted(unknown)}; known: ["grant", "workstation"]'
        raise ValueError(msg)
    workstation = str(raw.get('workstation', '')).strip()
    if not workstation or any(c.isspace() or c == ',' for c in workstation):
        msg = f'{source}: workstation must be a non-empty name without spaces or commas, got {workstation!r}'
        raise ValueError(msg)
    grants = tuple(_grant(g, source) for g in raw.get('grant', []))
    if not grants:
        msg = f'{source}: no [[grant]] -- this machine holds no share of any account'
        raise ValueError(msg)
    seen: set[str] = set()
    for grant in grants:
        if grant.account in seen:
            msg = f'{source}: account {grant.account!r} is granted twice; one share per account'
            raise ValueError(msg)
        seen.add(grant.account)
    return Machine(workstation=workstation, grants=grants)
