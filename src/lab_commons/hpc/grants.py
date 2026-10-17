"""Which cluster accounts THIS MACHINE may use -- the ``[hpc]`` table of the machine file.

A GRANT IS AN ACCOUNT THIS BOX MAY REACH, NEVER A SHARE OF IT. The accounts are ONE POOL: no session owns
one, and admission fits each job against what the account has free now. What each SESSION is USING is read
from Slurm at plan time, never stored, because every job a session submits carries
``--comment=lc:session=<name>`` and ``squeue`` reports it back on every account
(:mod:`lab_commons.hpc.slurm`). A stored usage figure would be a second copy of Slurm's own state, wrong
the moment a job ends.

A SESSION HAS A CAP, NOT AN ACCOUNT (user ruling 2026-10-17). ``$LC_HPC_SESSION`` names the session this
process submits for; its ``[hpc.session.<name>]`` table is the one declaration of what it may hold at once
across the whole pool -- tasks, CPUs and memory, pending tasks included -- and, optionally, the most one
task may ask. Past the cap a job WAITS (:func:`lab_commons.hpc.plan.allocate` gives it no room); only a task
past the per-task ceiling is refused. No ``$LC_HPC_SESSION`` tags nothing and caps nothing; a name with no
table is refused.

AN ACCOUNT IS ``(user, slurm_account)``, REACHED THROUGH ANY OF ITS LOGIN HOSTS. ``hosts`` is ordered: the
runner (:func:`lab_commons.hpc.cluster.runner_for`) tries each with ``BatchMode`` ssh and a short connect timeout
and uses the first that answers. Every host of one grant shares Slurm and the home directory, so a run
submitted through one is read back through another. Quota is per account; a session's tags span them all.

NO SECRETS. ``user@host`` is the ``ssh`` target; keys, ports and jump hosts stay in ``~/.ssh/config``.

The ``[hpc]`` table of the per-machine file (:mod:`lab_commons.config`)::

    [hpc]
    [[hpc.grant]]
    user = "me"
    hosts = ["login1.cluster.example", "login2.cluster.example"]   # tried in order, >= 1
    slurm_account = "acct-free"
    priority = 0                                  # optional; breaks a tie between equally fast grants
    partitions = ["shortq", "defq"]               # optional cluster facts: candidates, in preference order
    qos = { devq = "dev" }                        # optional: partitions that need a --qos
    setup = ["module load git/2.42.0"]   # optional: run first in every array task

    [hpc.session.wdg-design]             # one per session; $LC_HPC_SESSION picks this process's
    tasks = 2                            # concurrent tasks, pending + running, across every account
    cpus = 128
    mem_gb = 600
    task_cpus = 64                       # optional per-task ceilings: a bigger task is refused
    task_mem_gb = 300

``setup`` IS THE CLUSTER'S, NOT THE JOB'S. A compute node's environment is a fact of the cluster -- a real cluster.s
compute nodes have no ``git`` until a module is loaded (measured 2026-10-09: 126 git-calling tests errored
``FileNotFoundError: 'git'`` there, while the login node has it) -- so it is declared once per grant and
prepended to every shard script that grant submits, before the job's own set-up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Final

from lab_commons.config import config_path, section
from lab_commons.hpc.config import Cluster
from lab_commons.hpc.retired import RETIRED

__all__ = ['COMMENT_PREFIX', 'SESSION_ENV', 'Grant', 'Machine', 'Session', 'load_grants']

#: Every submitted job's ``--comment`` starts with this, followed by the session name.
COMMENT_PREFIX: Final = 'lc:session='

#: The session this process submits for; the one source of the tag.
SESSION_ENV: Final = 'LC_HPC_SESSION'

#: Keys of ``[hpc]`` that were retired, each with its remedy.
_RETIRED_KEYS: Final = {
    'workstation': f'jobs are tagged by session (${SESSION_ENV}, capped by [hpc.session.<name>]) -- delete the line',
}


@dataclass(frozen=True)
class Session:
    """What one session may hold at once across the whole pool, pending tasks included; per-task ceilings optional."""

    name: str
    tasks: int
    cpus: float
    mem_gb: float
    task_cpus: float | None = None
    task_mem_gb: float | None = None

    def __post_init__(self) -> None:
        """Every cap is positive."""
        caps = (self.tasks, self.cpus, self.mem_gb, self.task_cpus or 1, self.task_mem_gb or 1)
        if any(c <= 0 for c in caps):
            msg = f'every session cap is positive, got {self}'
            raise ValueError(msg)


def _session(raw: dict[str, Any], source: str) -> Session | None:
    """``$LC_HPC_SESSION``'s ``[hpc.session.<name>]``; ``None`` when unset. A name without a table is refused."""
    name = os.environ.get(SESSION_ENV, '').strip()
    if not name:
        return None
    if any(c.isspace() or c in ',;' for c in name):
        msg = f'${SESSION_ENV} must be a name without spaces, commas or semicolons, got {name!r}'
        raise ValueError(msg)
    table = raw.get(name)
    if table is None:
        msg = f'{source}: ${SESSION_ENV}={name} but there is no [hpc.session.{name}] declaring its cap'
        raise ValueError(msg)
    known = set(Session.__dataclass_fields__) - {'name'}
    if unknown := set(table) - known:
        msg = f'{source}: [hpc.session.{name}] has unknown keys {sorted(unknown)}; known: {sorted(known)}'
        raise ValueError(msg)
    try:
        return Session(name=name, **table)
    except TypeError as missing:
        msg = f'{source}: [hpc.session.{name}] needs tasks, cpus and mem_gb: {missing}'
        raise ValueError(msg) from missing


@dataclass(frozen=True)
class Grant:
    """One share of one Slurm account, reached over ``ssh`` as ``user`` at the first answering host."""

    user: str
    hosts: tuple[str, ...]
    slurm_account: str
    priority: int = 0
    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)
    setup: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A grant names who, where and which account."""
        if not self.user or not self.slurm_account or not self.hosts or not all(self.hosts):
            msg = f'a grant names its user, at least one login host and its slurm_account, got {self}'
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
        """The planner's view of this grant: which partitions it may ask for, and under which QOS."""
        return Cluster(partitions=self.partitions, qos=self.qos)


@dataclass(frozen=True)
class Machine:
    """The session this process submits for, and every grant the machine holds."""

    session: Session | None
    grants: tuple[Grant, ...]

    @property
    def comment(self) -> str:
        """The ``--comment`` every job of this session carries; ``''`` without one."""
        return COMMENT_PREFIX + self.session.name if self.session else ''


_GRANT_KEYS: Final = frozenset(Grant.__dataclass_fields__)


def _grant(raw: dict[str, Any], source: str) -> Grant:
    for key in raw:
        if (remedy := RETIRED.get(f'[[hpc.grant]] {key}')) is not None:
            msg = f'{source}: [[hpc.grant]] {key} is retired: {remedy}'
            raise ValueError(msg)
    unknown = set(raw) - _GRANT_KEYS
    if unknown:
        msg = f'{source}: [[hpc.grant]] has unknown keys {sorted(unknown)}; known: {sorted(_GRANT_KEYS)}'
        raise ValueError(msg)
    return Grant(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})


def load_grants(table: dict[str, Any] | None = None) -> Machine:
    """Read the ``[hpc]`` table (default: this machine's). No table or a duplicate account is refused."""
    source = f'{config_path()} [hpc]'
    raw = section('hpc') if table is None else table
    if not raw:
        msg = f'{source}: no [hpc] table -- write one (see lab_commons.hpc.grants) to give this machine a grant'
        raise ValueError(msg)
    for key, remedy in _RETIRED_KEYS.items():
        if key in raw:
            msg = f'{source}: [hpc] {key} is retired: {remedy}'
            raise ValueError(msg)
    unknown = set(raw) - {'grant', 'session'}
    if unknown:
        msg = f'{source}: unknown keys {sorted(unknown)}; known: ["grant", "session"]'
        raise ValueError(msg)
    grants = tuple(_grant(g, source) for g in raw.get('grant', []))
    if not grants:
        msg = f'{source}: no [[hpc.grant]] -- this machine reaches no account'
        raise ValueError(msg)
    seen: set[tuple[str, str]] = set()
    for grant in grants:
        if grant.account in seen:
            msg = f'{source}: account {grant.account!r} is granted twice; one grant per account'
            raise ValueError(msg)
        seen.add(grant.account)
    return Machine(session=_session(raw.get('session', {}), source), grants=grants)
