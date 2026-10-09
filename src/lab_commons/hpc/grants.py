"""Which cluster accounts THIS MACHINE may use, and how much of each -- the ``[hpc]`` table of the machine file.

A GRANT IS A SHARE OF AN ACCOUNT, HELD BY A BOX. One workstation may hold several grants (two clusters,
two Slurm accounts); one login may be shared by several workstations, each holding its own share of it.
The file says only what is agreed -- the share; what each box is USING is read from Slurm at plan time,
never stored, because every job a box submits carries ``--comment=lc:ws=<workstation>`` and ``squeue``
reports it back (:mod:`lab_commons.hpc.slurm`). A stored usage figure would be a second copy of Slurm's
own state, wrong the moment a job ends.

AN ACCOUNT IS ``(user, slurm_account)``, REACHED THROUGH ANY OF ITS LOGIN HOSTS. ``hosts`` is ordered: the
runner (:func:`lab_commons.hpc.run.runner_for`) tries each with ``BatchMode`` ssh and a short connect timeout
and uses the first that answers. Every host of one grant shares Slurm and the home directory, so a run
submitted through one is read back through another. Quota and the ``lc:ws=`` usage tags are per account.

NO SECRETS. ``user@host`` is the ``ssh`` target; keys, ports and jump hosts stay in ``~/.ssh/config``.

The ``[hpc]`` table of the per-machine file (:mod:`lab_commons.config`)::

    [hpc]
    workstation = "lab-ws-07"                     # stable, unique among the boxes sharing an account

    [[hpc.grant]]
    user = "me"
    hosts = ["login1.cluster.example", "login2.cluster.example"]   # tried in order, >= 1
    slurm_account = "acct-free"
    cpus = 64                                     # this box's share of the account's CPU quota
    priority = 0                                  # optional; breaks a tie between equally fast grants
    partitions = ["shortq", "defq"]               # optional cluster facts: candidates, in preference order
    qos = { devq = "dev" }                        # optional: partitions that need a --qos
    setup = ["module load git-uoneasy/2.42.0-GCCcore-13.2.0"]   # optional: run first in every array task

``setup`` IS THE CLUSTER'S, NOT THE JOB'S. A compute node's environment is a fact of the cluster -- Ada's
compute nodes have no ``git`` until a module is loaded (measured 2026-10-09: 126 git-calling tests errored
``FileNotFoundError: 'git'`` there, while the login node has it) -- so it is declared once per grant and
prepended to every shard script that grant submits, before the job's own set-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from lab_commons.config import config_path, section
from lab_commons.hpc.config import Cluster

__all__ = ['COMMENT_PREFIX', 'Grant', 'Machine', 'load_grants']

#: Every submitted job's ``--comment`` starts with this, followed by the workstation name.
COMMENT_PREFIX: Final = 'lc:ws='


@dataclass(frozen=True)
class Grant:
    """One share of one Slurm account, reached over ``ssh`` as ``user`` at the first answering host."""

    user: str
    hosts: tuple[str, ...]
    slurm_account: str
    cpus: int
    priority: int = 0
    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)
    setup: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A grant names who, where and which account; a share that cannot hold one CPU is not a share."""
        if not self.user or not self.slurm_account or not self.hosts or not all(self.hosts):
            msg = f'a grant names its user, at least one login host and its slurm_account, got {self}'
            raise ValueError(msg)
        if self.cpus < 1:
            msg = f'grant {self.name}: a share must be positive, got cpus={self.cpus}'
            raise ValueError(msg)

    @property
    def account(self) -> tuple[str, str]:
        """``(user, slurm_account)`` -- what quota and usage are counted against."""
        return self.user, self.slurm_account

    @property
    def name(self) -> str:
        """``user@first-host (slurm_account)`` -- how messages and records name this grant."""
        return f'{self.user}@{self.hosts[0]} ({self.slurm_account})'

    @property
    def targets(self) -> tuple[str, ...]:
        """The ``ssh`` targets, in the order they are tried."""
        return tuple(f'{self.user}@{host}' for host in self.hosts)

    def same_cluster(self, other: Grant) -> bool:
        """Whether *other* reaches the same cluster -- they share a login host."""
        return not set(self.hosts).isdisjoint(other.hosts)

    def cluster(self) -> Cluster:
        """The planner's view of this grant: where to connect and what to ask for."""
        return Cluster(host=self.hosts[0], partitions=self.partitions, qos=self.qos, account=self.slurm_account)


@dataclass(frozen=True)
class Machine:
    """A workstation's name and every grant it holds."""

    workstation: str
    grants: tuple[Grant, ...]

    @property
    def comment(self) -> str:
        """The ``--comment`` every job of this machine carries."""
        return COMMENT_PREFIX + self.workstation


_GRANT_KEYS: Final = frozenset(Grant.__dataclass_fields__)


def _grant(raw: dict[str, Any], source: str) -> Grant:
    unknown = set(raw) - _GRANT_KEYS
    if unknown:
        msg = f'{source}: [[hpc.grant]] has unknown keys {sorted(unknown)}; known: {sorted(_GRANT_KEYS)}'
        raise ValueError(msg)
    return Grant(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})


def load_grants(table: dict[str, Any] | None = None) -> Machine:
    """Read the ``[hpc]`` table (default: this machine's). No table, a duplicate account or a bad share is refused."""
    source = f'{config_path()} [hpc]'
    raw = section('hpc') if table is None else table
    if not raw:
        msg = f'{source}: no [hpc] table -- write one (see lab_commons.hpc.grants) to give this machine a grant'
        raise ValueError(msg)
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
        msg = f'{source}: no [[hpc.grant]] -- this machine holds no share of any account'
        raise ValueError(msg)
    seen: set[tuple[str, str]] = set()
    for grant in grants:
        if grant.account in seen:
            msg = f'{source}: account {grant.account!r} is granted twice; one share per account'
            raise ValueError(msg)
        seen.add(grant.account)
    return Machine(workstation=workstation, grants=grants)
