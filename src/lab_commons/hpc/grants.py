"""Which cluster accounts THIS MACHINE may use -- the ``[hpc]`` table of the machine file.

A GRANT IS AN ACCOUNT THIS BOX MAY REACH, NEVER A SHARE OF IT. The accounts are ONE POOL: no session owns
one, and admission fits each job against what the account has free now. What each SESSION is USING is read
from Slurm at plan time, never stored, because every job a session submits carries
``--comment=lc:session=<name>`` and ``squeue`` reports it back on every account
(:mod:`lab_commons.hpc.slurm`). A stored usage figure would be a second copy of Slurm's own state, wrong
the moment a job ends.

A SESSION HAS A SHARE, NOT AN ACCOUNT, AND NO MACHINE FILE HOLDS IT (user ruling 2026-10-17).
``$LC_HPC_SESSION`` names the session this process submits for. What it may hold at once across the whole
pool is derived from Slurm at plan time, or set by one expiring forge record
(:mod:`lab_commons.hpc.allocation`) -- an allocation between sessions is temporary. Past its share a job
WAITS (:func:`lab_commons.hpc.plan.allocate` gives it no room); only a task past a per-task ceiling is
refused. No ``$LC_HPC_SESSION`` tags nothing and caps nothing.

AN ACCOUNT IS ``(user, slurm_account)`` ON ONE DECLARED CLUSTER. A cluster's facts -- its login hosts, partitions,
QOS, compute-node set-up, architecture, where an account's trees live, and which of its Slurm accounts are billed --
are declared ONCE under ``[hpc.clusters.<name>]``; a grant names its cluster and inherits them, so no fact of a
cluster is repeated per account. ``hosts`` is ordered: the runner (:func:`lab_commons.hpc.cluster.runner_for`)
tries each with ``BatchMode`` ssh and a short connect timeout and uses the first that answers. Every host of one
cluster shares Slurm and the home directory, so a run submitted through one is read back through another.

A MACHINE REACHES AN ACCOUNT BY ITS ROLE (HPC-ACCESS-BY-ROLE). Every grant declares a ``role``; the machine's
``[hpc] reach`` lists the roles it may use, and :func:`load_grants` keeps only the grants whose role it lists.
An account another role reaches is REFUSED by name (:class:`AccessRefused`), never silently skipped, and a machine
whose reach holds no grant is refused outright. What a role DOES is its reader's (``[hpc.roles]``).

NO SECRETS. ``user@host`` is the ``ssh`` target; keys, ports and jump hosts stay in ``~/.ssh/config``.

The ``[hpc]`` table (:mod:`lab_commons.config`; the declarations usually come from the family layer)::

    [hpc]
    reach = ["research"]                 # the roles THIS machine may use; [] reaches nothing

    [hpc.clusters.<name>]
    hosts = ["login1.cluster.example", "login2.cluster.example"]   # tried in order, >= 1
    partitions = ["shortq", "defq"]      # optional: candidates, in preference order
    qos = { devq = "dev" }               # optional: partitions that need a --qos
    setup = ["module load git/2.42.0"]   # optional: run first in every array task
    architecture = "site-arch"           # optional: its entry in the cluster catalog (lab_commons.hpc.topology)
    root = "/work/proj/{user}"           # optional: an account's trees, when compute nodes cannot see home

    [hpc.clusters.<name>.slurm_accounts.<slurm account>]
    billed = false                       # true: every job here spends money
    budget = { core_hours = 1000, since = "2026-01-01" }   # a billed account's TOTAL allocation

    [[hpc.grant]]
    user = "me"
    cluster = "<name>"
    slurm_account = "acct-free"          # one of the cluster's [slurm_accounts]
    role = "research"                    # matched against [hpc] reach
    owner = "A. Person"                  # optional: who holds the account
    priority = 0                         # optional; breaks a tie between equally fast grants

    [hpc.paging]                         # optional; undeclared, placement ignores size and region
    page_cpus = 8                        # one page: a SMALL task fits it
    page_mem_gb = 20
    large_pages = 4                      # more pages than this is LARGE; between, MEDIUM
    movable_minutes = 30                 # a rerun-safe task this short may borrow the large-job region

A BILLED ACCOUNT IS CHARGED AGAINST ITS BUDGET BEFORE EVERY ``sbatch`` (:mod:`lab_commons.hpc.budget`): what it
has spent since ``since`` plus what its queued work may still spend plus this ask must fit ``core_hours``, or the
submission is refused; a billed account with no budget is refused. A free account is never budget-checked.

PAGING IS OS MEMORY MANAGEMENT OVER THE POOL (user design 2026-10-17). Tasks are page-sized so free memory
is never shattered into holes no task fits; the emptiest account is the large-job region, borrowed only by
small movable tasks, and drained -- never killed -- while a large job waits (:func:`lab_commons.hpc.region.region`).

A BILLED GRANT RUNS ONLY WHAT NO FREE GRANT CAN HOLD (user ruling 2026-10-27). Work is spanned over the
free grants alone; only when they cannot hold its wall -- the span refuses, or an item's padded wall exceeds
every free partition+QOS ceiling -- is it spanned over the billed grants alone, never a mix. A free-grant
OUTAGE is not that: with free grants declared and none answering, the work is refused
rather than billed (:func:`lab_commons.hpc.jobs.tiered`).

``setup`` IS THE CLUSTER'S, NOT THE JOB'S. A compute node's environment is a fact of the cluster -- a real cluster's
compute nodes have no ``git`` until a module is loaded (measured 2026-10-09: 126 git-calling tests errored
``FileNotFoundError: 'git'`` there, while the login node has it) -- so it is declared once per cluster and
prepended to every shard script its grants submit, before the job's own set-up.
"""

from __future__ import annotations

import importlib
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from lab_commons.config import config_path, section
from lab_commons.hpc.config import Cluster, Cost
from lab_commons.hpc.retired import RETIRED

__all__ = [
    'ACCESS_RULE', 'COMMENT_PREFIX', 'SESSION_ENV', 'AccessRefused', 'Budget', 'Grant', 'Machine', 'Paging', 'Session',
    'check_target', 'declared_grants', 'load_grants',
]  # fmt: skip

#: Every submitted job's ``--comment`` starts with this, followed by the session name.
COMMENT_PREFIX: Final = 'lc:session='

#: The session this process submits for; the one source of the tag.
SESSION_ENV: Final = 'LC_HPC_SESSION'

#: The rule a refusal of an unreached account names.
ACCESS_RULE: Final = 'HPC-ACCESS-BY-ROLE'

#: Keys of ``[hpc]`` that were retired, each with its remedy.
_RETIRED_KEYS: Final = {
    'workstation': f'jobs are tagged by session (${SESSION_ENV}) -- delete the line',
    'session': (
        'a session share is derived from Slurm, or overridden by one expiring forge record '
        '(python -m dev_lab.dev.hpcaccount allocate) -- delete the table'
    ),
}


@dataclass(frozen=True)
class Session:
    """What one session may hold at once across the whole pool, pending tasks included.

    Caps ``None`` are DERIVED at plan time (:func:`lab_commons.hpc.allocation.derived`) -- of the whole pool, or
    of what the override record's *fixed* named shares leave (:data:`lab_commons.hpc.allocation.RECORD`).
    Per-task ceilings are optional either way.
    """

    name: str
    cpus: float | None = None
    mem_gb: float | None = None
    tasks: int | None = None
    task_cpus: float | None = None
    task_mem_gb: float | None = None
    #: Under an override that does not name this session: each named session's fixed ``(name, cpus, mem_gb)``.
    fixed: tuple[tuple[str, float, float], ...] = ()

    def __post_init__(self) -> None:
        """No cap is negative; a per-task ceiling is positive."""
        caps = (self.tasks, self.cpus, self.mem_gb)
        ceilings = (self.task_cpus, self.task_mem_gb)
        if any(c is not None and c < 0 for c in caps) or any(c is not None and c <= 0 for c in ceilings):
            msg = f'a session cap is never negative and a per-task ceiling is positive, got {self}'
            raise ValueError(msg)


@dataclass(frozen=True)
class Paging:
    """The page every task is sized in, where LARGE begins, and the wall under which a rerun-safe task is movable."""

    page_cpus: int
    page_mem_gb: float
    large_pages: int
    movable_minutes: float

    def __post_init__(self) -> None:
        """Every bound is positive."""
        if min(self.page_cpus, self.page_mem_gb, self.large_pages, self.movable_minutes) <= 0:
            msg = f'every paging bound is positive, got {self}'
            raise ValueError(msg)

    def pages(self, cpus: float, mem_gb: float) -> int:
        """Pages a task of *cpus* and *mem_gb* occupies -- the larger of its two dimensions."""
        return max(1, math.ceil(cpus / self.page_cpus), math.ceil(mem_gb / self.page_mem_gb))

    def size(self, cost: Cost) -> str:
        """``small`` (one page), ``large`` (more than ``large_pages``) or ``medium``."""
        pages = self.pages(cost.cpus, cost.mem_gb)
        return 'small' if pages == 1 else 'large' if pages > self.large_pages else 'medium'


def _paging(raw: dict[str, Any] | None, source: str) -> Paging | None:
    """``[hpc.paging]``, every key required; ``None`` when undeclared."""
    if raw is None:
        return None
    known = set(Paging.__dataclass_fields__)
    if set(raw) != known:
        msg = f'{source}: [hpc.paging] declares exactly {sorted(known)}, got {sorted(raw)}'
        raise ValueError(msg)
    return Paging(**raw)


def _session(source: str) -> Session | None:
    """``$LC_HPC_SESSION``'s session, its caps derived; ``None`` when unset."""
    name = os.environ.get(SESSION_ENV, '').strip()
    if not name:
        return None
    if any(c.isspace() or c in ',;' for c in name):
        msg = f'{source}: ${SESSION_ENV} must be a name without spaces, commas or semicolons, got {name!r}'
        raise ValueError(msg)
    return Session(name)


_ISO_DATE: Final = re.compile(r'\d{4}-\d{2}-\d{2}')


@dataclass(frozen=True)
class Budget:
    """A billed Slurm account's TOTAL allocation: *core_hours* from the ISO date *since* on."""

    core_hours: float
    since: str

    def __post_init__(self) -> None:
        """A budget is positive and starts on a date."""
        if not self.core_hours > 0 or not _ISO_DATE.fullmatch(str(self.since)):
            msg = f'a budget is positive core_hours since an ISO date (YYYY-MM-DD), got {self}'
            raise ValueError(msg)


@dataclass(frozen=True)
class Grant:
    """One Slurm account on one cluster, reached over ``ssh`` as ``user`` at the first answering host.

    The cluster's facts (hosts, partitions, qos, setup, architecture, root, billed, budget) are copied from its
    ``[hpc.clusters.<name>]`` by :func:`load_grants`; a grant table never states them.
    """

    user: str
    hosts: tuple[str, ...]
    slurm_account: str
    priority: int = 0
    partitions: tuple[str, ...] = ()
    qos: dict[str, str] = field(default_factory=dict)
    setup: tuple[str, ...] = ()
    #: This account's compute costs money: it runs only work no free grant can hold, inside its :attr:`budget`.
    billed: bool = False
    #: A billed account's total allocation; ``None``: undeclared, and a billed submission is then refused.
    budget: Budget | None = None
    #: The cluster catalog's architecture this account runs on (:mod:`lab_commons.hpc.topology`); ``''``: none.
    architecture: str = ''
    #: The role this account plays, matched against ``[hpc] reach``; what a role DOES is ``[hpc.roles]``' reader's.
    role: str = ''
    #: The declared cluster's name; ``''`` for a grant built in code.
    cluster_name: str = ''
    #: Where this account's trees live when its compute nodes cannot see home; ``''``: under home.
    root: str = ''
    #: Who holds the account; ``''``: undeclared.
    owner: str = ''

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
class Affinity:
    """Which accounts produce forge verify parts, and whether research may borrow them (user ruling 2026-10-30).

    Data, never declared here: the forge's host cards carry *verify* and its reader supplies it; *borrow* is the
    user's unexpired opt-in in the allocation record (:func:`lab_commons.hpc.affinity.resolve`). Each is a
    grant's ``user``.
    """

    #: The verify accounts: a research submission never lands on one by default.
    verify: frozenset[str] = frozenset()
    #: The user opted in to research borrowing them (:data:`lab_commons.hpc.allocation.RESEARCH_BORROWS_VERIFY`).
    borrow: bool = False


class AccessRefused(PermissionError):
    """An account this machine's ``[hpc] reach`` does not reach was asked for; the message names rule and remedy."""


def _refusal(grant: Grant, reach: frozenset[str]) -> str:
    return (
        f'{grant.user} ({grant.cluster_name} {grant.slurm_account}) is a {grant.role!r} account and this machine '
        f'reaches {sorted(reach)} -- {ACCESS_RULE}: a machine uses only the accounts whose role its declaration '
        f"lists. Run it from a machine that reaches {grant.role!r}, or change this machine's declared reach in the "
        'config source, never by a local edit'
    )


@dataclass(frozen=True)
class Machine:
    """The session this process submits for, and every grant the machine REACHES."""

    session: Session | None
    grants: tuple[Grant, ...]
    paging: Paging | None = None
    #: The verify accounts a research submission yields (:mod:`lab_commons.hpc.affinity`); ``None``: none known.
    affinity: Affinity | None = None
    #: ``[hpc.roles.<name>]`` as written, carried UNINTERPRETED: the roles' meaning is the reader's (dev-lab).
    roles: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: The declared grants this machine's reach does NOT hold: named by :meth:`grant`'s refusal, never used.
    refused: tuple[Grant, ...] = ()
    #: The roles this machine reaches (``[hpc] reach``, plus what a verified session role adds).
    reach: frozenset[str] = frozenset()
    #: Per refused role: how a session comes to reach it (``[hpc] session_reach``); ``''``: no session role does.
    hints: Mapping[str, str] = field(default_factory=dict)

    @property
    def comment(self) -> str:
        """The ``--comment`` every job of this session carries; ``''`` without one."""
        return COMMENT_PREFIX + self.session.name if self.session else ''

    def grant(self, user: str) -> Grant:
        """The reached grant of *user*.

        Raises:
            AccessRefused: *user* is a declared account another role reaches.
            LookupError: no grant declares *user*.

        """
        for grant in self.grants:
            if grant.user == user:
                return grant
        for grant in self.refused:
            if grant.user == user:
                raise AccessRefused(_refusal(grant, self.reach) + self.hints.get(grant.role, ''))
        msg = f'no [[hpc.grant]] declares user {user!r}; reached: {sorted({g.user for g in self.grants})}'
        raise LookupError(msg)


#: The keys a ``[[hpc.grant]]`` table may carry: who, which cluster and account, its role. The rest is the cluster's.
_GRANT_KEYS: Final = frozenset({'user', 'cluster', 'slurm_account', 'role', 'owner', 'priority'})
#: The keys of one ``[hpc.clusters.<name>]``.
_CLUSTER_KEYS: Final = frozenset({'hosts', 'partitions', 'qos', 'setup', 'architecture', 'root', 'slurm_accounts'})
#: The keys of one ``[hpc.clusters.<name>.slurm_accounts.<account>]``.
_ACCOUNT_KEYS: Final = frozenset({'billed', 'budget'})
#: The keys of ``[hpc]``.
_HPC_KEYS: Final = frozenset(
    {'reach', 'session_reach', 'session_verifier', 'clusters', 'grant', 'paging', 'roles', 'ssh'}
)


def _refuse_keys(raw: Mapping[str, Any], known: frozenset[str], where: str, source: str) -> None:
    for key in raw:
        if (remedy := RETIRED.get(f'{where} {key}')) is not None:
            msg = f'{source}: {where} {key} is retired: {remedy}'
            raise ValueError(msg)
    if unknown := set(raw) - known:
        msg = f'{source}: {where} has unknown keys {sorted(unknown)}; known: {sorted(known)}'
        raise ValueError(msg)


def _account(facts: object, where: str, source: str) -> tuple[bool, Budget | None]:
    if not isinstance(facts, dict) or not isinstance(facts.get('billed'), bool):
        msg = f'{source}: {where} declares billed = true|false'
        raise ValueError(msg)  # noqa: TRY004 -- a declaration defect, refused like every other
    _refuse_keys(facts, _ACCOUNT_KEYS, where, source)
    budget = facts.get('budget')
    if budget is not None and not facts['billed']:
        msg = f'{source}: {where} is free: a budget belongs to a billed account'
        raise ValueError(msg)
    return facts['billed'], None if budget is None else Budget(**budget)


def _clusters(raw: object, source: str) -> dict[str, dict[str, Any]]:
    """Each declared cluster's table, its Slurm accounts resolved to ``{account: (billed, budget)}``."""
    if not isinstance(raw, dict) or not all(isinstance(table, dict) for table in raw.values()):
        msg = f'{source}: [hpc.clusters] holds one [hpc.clusters.<name>] table per cluster'
        raise ValueError(msg)
    clusters = {}
    for name, table in raw.items():
        where = f'[hpc.clusters.{name}]'
        _refuse_keys(table, _CLUSTER_KEYS, where, source)
        accounts = table.get('slurm_accounts')
        if not table.get('hosts') or not isinstance(accounts, dict) or not accounts:
            msg = f'{source}: {where} declares its login hosts and at least one slurm_accounts.<account>'
            raise ValueError(msg)
        resolved = {acct: _account(facts, f'{where} slurm_accounts.{acct}', source) for acct, facts in accounts.items()}
        clusters[name] = {**table, 'slurm_accounts': resolved}
    return clusters


def _grant(raw: dict[str, Any], clusters: Mapping[str, Mapping[str, Any]], source: str) -> Grant:
    _refuse_keys(raw, _GRANT_KEYS, '[[hpc.grant]]', source)
    user, name, account = raw.get('user', ''), raw.get('cluster', ''), raw.get('slurm_account', '')
    if not raw.get('role'):
        msg = f'{source}: [[hpc.grant]] {user!r} declares no role; a machine reaches an account only by its role'
        raise ValueError(msg)
    if name not in clusters:
        msg = f'{source}: [[hpc.grant]] {user!r} names cluster {name!r}; declared: {sorted(clusters)}'
        raise ValueError(msg)
    cluster = clusters[name]
    if account not in cluster['slurm_accounts']:
        msg = (
            f'{source}: [[hpc.grant]] {user!r} names slurm_account {account!r}, not one of '
            f'[hpc.clusters.{name}] slurm_accounts {sorted(cluster["slurm_accounts"])}'
        )
        raise ValueError(msg)
    billed, budget = cluster['slurm_accounts'][account]
    return Grant(
        user=user,
        hosts=tuple(cluster['hosts']),
        slurm_account=account,
        priority=raw.get('priority', 0),
        partitions=tuple(cluster.get('partitions', ())),
        qos=dict(cluster.get('qos', {})),
        setup=tuple(cluster.get('setup', ())),
        billed=billed,
        budget=budget,
        architecture=cluster.get('architecture', ''),
        role=raw['role'],
        cluster_name=name,
        root=cluster.get('root', '').replace('{user}', user),
        owner=raw.get('owner', ''),
    )


def _roles_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(role, str) and role for role in value)


def _session_roles(raw: Mapping[str, Any], source: str) -> frozenset[str]:
    """The session roles the declared verifier proves THIS session holds; none without a verifier."""
    spec = raw.get('session_verifier', '')
    if not spec:
        return frozenset()
    module, _, name = str(spec).partition(':')
    try:
        held = getattr(importlib.import_module(module), name)()
    except (ImportError, AttributeError) as missing:
        msg = f'{source}: [hpc] session_verifier {spec!r} does not load ({missing}); a session role is never assumed'
        raise ValueError(msg) from None
    return frozenset(held)


def _reach(raw: Mapping[str, Any], source: str) -> frozenset[str]:
    """The roles this machine reaches: its own ``reach``, plus what a VERIFIED session role adds on top.

    A session role never widens a machine that reaches nothing: with ``reach = []`` it adds nothing.
    """
    reach, by_session = raw.get('reach'), raw.get('session_reach', {})
    if not _roles_list(reach):
        msg = (
            f'{source}: [hpc] reach must list the roles this machine may use (["research"], or [] for none) -- '
            f'{ACCESS_RULE}: undeclared, it reaches nothing'
        )
        raise ValueError(msg)
    if not isinstance(by_session, dict) or not all(_roles_list(roles) for roles in by_session.values()):
        msg = f'{source}: [hpc] session_reach maps a session role to the account roles it adds, e.g. {{ x = ["y"] }}'
        raise ValueError(msg)
    if not reach:
        return frozenset()
    held = _session_roles(raw, source) & set(by_session)
    return frozenset(reach).union(*(by_session[role] for role in held))


def _hint(raw: Mapping[str, Any], role: str) -> str:
    """How a session comes to reach *role*, when a session role adds it."""
    adders = sorted(name for name, roles in raw.get('session_reach', {}).items() if role in roles)
    return f' (a session reaches it by holding the {" or ".join(adders)} session role)' if adders else ''


def _table(table: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    return f'{config_path()} [hpc]', section('hpc') if table is None else table


def declared_grants(table: dict[str, Any] | None = None) -> tuple[tuple[Grant, ...], frozenset[str]]:
    """EVERY grant the ``[hpc]`` table declares, reached or not, and the machine's reach; a defect refused."""
    source, raw = _table(table)
    if not raw:
        msg = f'{source}: no [hpc] table -- write one (see lab_commons.hpc.grants) to give this machine a grant'
        raise ValueError(msg)
    for key, remedy in _RETIRED_KEYS.items():
        if key in raw:
            msg = f'{source}: [hpc] {key} is retired: {remedy}'
            raise ValueError(msg)
    _refuse_keys(raw, _HPC_KEYS, '[hpc]', source)
    roles = raw.get('roles', {})
    if not isinstance(roles, dict) or not all(isinstance(row, dict) for row in roles.values()):
        msg = f'{source}: [hpc.roles] holds one [hpc.roles.<name>] table per role'
        raise ValueError(msg)
    clusters = _clusters(raw.get('clusters', {}), source)
    grants = tuple(_grant(g, clusters, source) for g in raw.get('grant', []))
    if not grants:
        msg = f'{source}: no [[hpc.grant]] -- this machine reaches no account'
        raise ValueError(msg)
    seen: set[tuple[str, str]] = set()
    for grant in grants:
        if grant.account in seen:
            msg = f'{source}: account {grant.account!r} is granted twice; one grant per account'
            raise ValueError(msg)
        seen.add(grant.account)
    return grants, _reach(raw, source)


def load_grants(table: dict[str, Any] | None = None) -> Machine:
    """The machine's REACHED grants, read from the ``[hpc]`` table (default: this machine's).

    Raises:
        AccessRefused: the reach holds none of the declared grants.
        ValueError: no table; a retired or unknown key; a grant naming an undeclared cluster or Slurm account, or
            no role; a duplicate account; an undeclared reach.

    """
    source, raw = _table(table)
    grants, reach = declared_grants(raw)
    reached = tuple(grant for grant in grants if grant.role in reach)
    if not reached:
        msg = f'{source}: [hpc] reach {sorted(reach)} holds none of the declared accounts -- {ACCESS_RULE}'
        raise AccessRefused(msg)
    return Machine(
        session=_session(source),
        grants=reached,
        paging=_paging(raw.get('paging'), source),
        roles=raw.get('roles', {}),
        refused=tuple(grant for grant in grants if grant.role not in reach),
        reach=reach,
        hints={grant.role: _hint(raw, grant.role) for grant in grants if grant.role not in reach},
    )


def check_target(target: str, table: dict[str, Any] | None = None) -> None:
    """Refuse an ``ssh`` *target* (``user@host``) that is a DECLARED account this machine does not reach.

    A door taking ``--at`` reaches its target directly; this keeps it inside the rule. A target no declaration
    names, or a machine with no ``[hpc]`` table (the account's own login node), is not this rule's to judge.
    """
    source, raw = _table(table)
    if not raw:
        return
    grants, reach = declared_grants(raw)
    for grant in grants:
        if target in grant.targets and grant.role not in reach:
            msg = f'{source}: {_refusal(grant, reach)}{_hint(raw, grant.role)}'
            raise AccessRefused(msg)
