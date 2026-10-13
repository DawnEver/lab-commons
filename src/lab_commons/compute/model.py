"""THE MODEL -- four nouns and one rule, PURE: no I/O, and nothing imported from ``dev`` or ``hpc``.

**Resources** are named, countable, non-negative quantities, and ALL ARE EQUAL: ``cpu``, ``mem_gb``,
``jmag``, ``femm``, ``windows``... A name a vector omits is zero. A host's **Supply** is its measured
vector plus the names PRESENT on it (a tool installed with every seat in use is present at 0). A
**Job** is ``(repo, sha, producer, needs)``; its **Demand** is its size. A **Reservation** is a lease
on one host or account (its ``scope``), ``for_`` one login, repo or need-set, until a moment.

THE RULE, for every host alike::

    admit(job) = needs(job) <= present(host)
             and demand(job) <= supply(host) - reservations not for job - running(host)

:func:`rank` decides ORDER (value per cost, aging, boosts); :func:`admit` decides FIT;
:func:`claimable` walks the order and claims what fits.

THE NUMBERS OF THE ORDER ARE DATA, declared once in ``[tool.lab_commons.schedule]`` and loaded by
:func:`lab_commons.dev.forgeschedule.load_schedule` into a :class:`Schedule`::

    value = base[class] + boost + aging_rate * wait_hours
    score = weight[repo] * value / expected_cost
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

__all__ = [
    'BRANCH',
    'INTEGRATION',
    'PR',
    'REQUEST',
    'Admission',
    'Candidate',
    'Demand',
    'For',
    'Job',
    'Ranked',
    'Reservation',
    'Resources',
    'Schedule',
    'Supply',
    'admit',
    'boost_of',
    'claimable',
    'expected_cost',
    'rank',
    'starved',
    'total',
    'value',
]

# --- resources, supply, demand ----------------------------------------------------------------------


class Resources(Mapping[str, float]):
    """An immutable vector of named non-negative amounts; a missing name reads 0 and a zero is not kept."""

    __slots__ = ('_amounts',)

    def __init__(self, amounts: Mapping[str, float] | None = None, /, **named: float) -> None:
        """*amounts* and *named* merged; a negative amount is refused by name."""
        merged = {**(amounts or {}), **named}
        negative = sorted(name for name, amount in merged.items() if amount < 0)
        if negative:
            msg = f'a resource amount is never negative: {negative}'
            raise ValueError(msg)
        self._amounts: dict[str, float] = {name: float(amount) for name, amount in merged.items() if amount > 0}

    def __getitem__(self, name: str) -> float:
        """The amount of *name*; 0 when absent."""
        return self._amounts.get(name, 0.0)

    def __iter__(self) -> Iterator[str]:
        """The non-zero names."""
        return iter(self._amounts)

    def __len__(self) -> int:
        """How many names are non-zero."""
        return len(self._amounts)

    def __hash__(self) -> int:
        """Equal vectors hash equal."""
        return hash(frozenset(self._amounts.items()))

    def __repr__(self) -> str:
        """The class and its non-zero amounts."""
        return f'{type(self).__name__}({self._amounts!r})'

    @property
    def names(self) -> frozenset[str]:
        """The names with a non-zero amount."""
        return frozenset(self._amounts)

    def __add__(self, other: Mapping[str, float]) -> Resources:
        """Name-wise sum."""
        return Resources({name: self[name] + other.get(name, 0.0) for name in self.names | set(other)})

    def __sub__(self, other: Mapping[str, float]) -> Resources:
        """FLOORED at zero: what is left can be nothing, never a debt."""
        return Resources({name: max(0.0, self[name] - other.get(name, 0.0)) for name in self.names})

    def __le__(self, other: Mapping[str, float]) -> bool:
        """Every name of this vector fits within *other*."""
        return not self.short_of(other)

    def short_of(self, other: Mapping[str, float]) -> tuple[str, ...]:
        """The names (sorted) this vector needs more of than *other* holds."""
        return tuple(sorted(name for name in self.names if self[name] > other.get(name, 0.0)))


def total(vectors: Iterable[Mapping[str, float]]) -> Resources:
    """The sum of *vectors*; nothing sums to the empty vector."""
    summed = Resources()
    for one in vectors:
        summed = summed + one
    return summed


class Demand(Resources):
    """A job's size: the same kind of vector as a supply, read as what the job takes while it runs."""

    __slots__ = ()


@dataclass(frozen=True)
class Supply:
    """A host's (or account's) measured vector now, and the names present on it; *scope* names it."""

    scope: str
    resources: Resources
    present: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Refuse an inconsistent value at construction."""
        if not self.present:
            object.__setattr__(self, 'present', self.resources.names)
        absent = sorted(self.resources.names - self.present)
        if absent:
            msg = f'{self.scope} counts {absent} it does not have present'
            raise ValueError(msg)


# --- jobs and reservations ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One unit of work: *producer* run at *sha* of *repo*, needing the resource names *needs*.

    *origin* is the forge login that asked for it ('' for a branch head) -- what a login reservation matches.
    """

    repo: str
    sha: str
    producer: str
    needs: frozenset[str] = frozenset()
    origin: str = ''


@dataclass(frozen=True)
class For:
    """Whom a reservation is for: exactly one of a login, a repo, or a need-set.

    A need-set matches every job whose needs CONTAIN it (a lease for ``{jmag}`` serves any JMAG job).
    """

    login: str = ''
    repo: str = ''
    needs: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """Refuse an inconsistent value at construction."""
        given = [bool(self.login), bool(self.repo), self.needs is not None]
        if sum(given) != 1:
            msg = f'a reservation is for exactly one login, repo or need-set, not {self!r}'
            raise ValueError(msg)

    def matches(self, job: Job) -> bool:
        """Whether *job* is one this reservation is for."""
        if self.login:
            return job.origin == self.login
        if self.repo:
            return job.repo == self.repo
        return self.needs is not None and self.needs <= job.needs


@dataclass(frozen=True)
class Reservation:
    """A lease of *resources* on *scope* (a host or an account) for ``for_``, until *until*, written by *by*."""

    scope: str
    resources: Resources
    for_: For
    until: datetime
    by: str

    def holds_against(self, job: Job, scope: str, now: datetime) -> bool:
        """Whether this lease withholds its resources from *job* on *scope* at *now*."""
        return self.scope == scope and now < self.until and not self.for_.matches(job)


# --- the rule -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    """The verdict of :func:`admit`: admitted, or the resource name(s) lacking -- never a bare False."""

    admitted: bool
    lacking: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        """Truthy exactly when admitted."""
        return self.admitted

    @property
    def why(self) -> str:
        """``''`` when admitted, else which resources are lacking."""
        return '' if self.admitted else f'lacking {", ".join(self.lacking)}'


def admit(
    job: Job,
    demand: Mapping[str, float],
    supply: Supply,
    *,
    reservations: Iterable[Reservation] = (),
    running: Iterable[Mapping[str, float]] = (),
    now: datetime,
) -> Admission:
    """THE RULE: needs present, and demand within supply less others' unexpired leases and what runs.

    A missing tool is named before any shortfall: no amount of free cpu makes FEMM installed.
    """
    missing = tuple(sorted(job.needs - supply.present))
    if missing:
        return Admission(admitted=False, lacking=missing)
    withheld = total(one.resources for one in reservations if one.holds_against(job, supply.scope, now))
    free = supply.resources - withheld - total(running)
    short = Resources(demand).short_of(free)
    return Admission(admitted=not short, lacking=short)


def claimable(
    ranked: Sequence[tuple[Job, Mapping[str, float]]],
    supply: Supply,
    *,
    reservations: Sequence[Reservation] = (),
    running: Sequence[Mapping[str, float]] = (),
    now: datetime,
) -> list[Job]:
    """The jobs to claim, walking *ranked* in its order and adding each claim to what runs.

    RANK ORDER IS AUTHORITATIVE, AND A NON-FIT DOES NOT STOP THE WALK: a job that does not fit is
    skipped and lower-ranked jobs that do fit are still claimed, so a large job never idles a host
    that has room for smaller ones. (Its starvation, if it lasts, is named by what it lacks.)
    """
    taken: list[Mapping[str, float]] = list(running)
    claimed = []
    for job, demand in ranked:
        if admit(job, demand, supply, reservations=reservations, running=taken, now=now):
            claimed.append(job)
            taken.append(demand)
    return claimed


# --- the order ------------------------------------------------------------------------------------------

#: The class of each candidate source the family knows; a repository may add classes of its own.
INTEGRATION: Final = 'integration'
PR: Final = 'pr'
BRANCH: Final = 'branch'
REQUEST: Final = 'request'

_PRIORITY: Final = re.compile(r'^priority:(?P<n>-?\d+(?:\.\d+)?)$')

#: Where the schedule's numbers are declared, for a refusal to name.
SCHEDULE_TABLE: Final = 'schedule'


@dataclass(frozen=True)
class Schedule:
    """``[tool.lab_commons.schedule]``: the family default; a repository overrides key by key."""

    classes: Mapping[str, float] = field(
        default_factory=lambda: {INTEGRATION: 100.0, PR: 60.0, BRANCH: 30.0, REQUEST: 20.0}
    )
    #: Value gained per hour a candidate waits -- the term that guarantees no class starves forever.
    aging_rate: float = 10.0
    starve_after_hours: Mapping[str, float] = field(
        default_factory=lambda: {INTEGRATION: 6.0, PR: 12.0, BRANCH: 24.0, REQUEST: 48.0}
    )
    #: Core-hours one ref of the class may use per 24 h; a class absent here is unbounded.
    daily_budget_core_h: Mapping[str, float] = field(default_factory=dict)
    #: Label -> value added; ``priority:<n>`` adds ``n`` without being declared.
    boosts: Mapping[str, float] = field(default_factory=lambda: {'priority:user': 1000.0})
    #: Opted-in child branches (fnmatch patterns), served as class ``branch`` beside the integration branch.
    branches: tuple[str, ...] = ()
    #: The label that makes an open issue a request.
    request_label: str = 'forge:request'
    #: The expected cost, in hours, of work nothing has measured yet.
    prior_cost_h: float = 4.0

    def base(self, kind: str) -> float:
        """The base value of class *kind*; an undeclared class is refused by name."""
        if kind not in self.classes:
            msg = f'class {kind!r} is not in [tool.lab_commons.{SCHEDULE_TABLE}] classes {sorted(self.classes)}'
            raise ValueError(msg)
        return float(self.classes[kind])


@dataclass(frozen=True)
class Candidate:
    """One unit of work waiting: a part a ref's newest commit lacks, or an issue request (``number``)."""

    repo: str
    ref: str
    sha: str
    platform: str
    kind: str
    since: datetime
    scope: str
    labels: tuple[str, ...] = ()
    number: int = 0
    origin: str = ''  # the forge login whose PR or request it is; '' for a branch head

    @property
    def key(self) -> str:
        """How a host card and an alarm name it: ``<ref>@<sha12>:<platform>``."""
        return f'{self.ref}@{self.sha[:12]}:{self.platform}'


@dataclass(frozen=True)
class Ranked:
    """A candidate with its numbers, and why it is not claimed (``''`` = nothing blocks it)."""

    candidate: Candidate
    value: float
    cost_h: float
    score: float
    wait_h: float
    blocked: str = ''


def boost_of(labels: Sequence[str], schedule: Schedule) -> float:
    """The value the user attached: every declared label's boost plus every ``priority:<n>``."""
    found = 0.0
    for label in labels:
        if label in schedule.boosts:
            found += float(schedule.boosts[label])
        elif (match := _PRIORITY.match(label)) is not None:
            found += float(match['n'])
    return found


def value(candidate: Candidate, schedule: Schedule, now: datetime) -> float:
    """``base[class] + boost + aging_rate * wait_hours`` (module docstring)."""
    wait_h = max(0.0, (now - candidate.since).total_seconds() / 3600)
    return schedule.base(candidate.kind) + boost_of(candidate.labels, schedule) + schedule.aging_rate * wait_h


def expected_cost(measured: Mapping[str, Any], prior_h: float, *, cores: int = 1) -> float:
    """Hours one full run costs: the last run's measured core-seconds (durations + overheads) over *cores*.

    On a cluster *cores* is 1 (core-hours); on a box it is the box's cores (wall-hours). Nothing measured
    is *prior_h*.
    """
    seconds = sum(dict(measured.get('durations', {})).values()) + sum(dict(measured.get('overheads', {})).values())
    return seconds / 3600 / max(1, cores) if seconds > 0 else prior_h


def rank(
    candidates: Sequence[Candidate],
    schedules: Mapping[str, Schedule],
    now: datetime,
    *,
    cost_h: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> tuple[Ranked, ...]:
    """Every candidate scored and ordered, highest score first. PURE: the one ordering rule.

    *schedules* and *cost_h* are keyed by repo; *weights* by repo, 1 for a repo it omits. A host's own
    user is served by its own reserve (:mod:`lab_commons.compute.ledger`), never by a lift in the order.
    """
    weights = weights or {}
    ranked = []
    for one in candidates:
        schedule = schedules[one.repo]
        worth = value(one, schedule, now)
        cost = max(cost_h.get(one.repo, schedule.prior_cost_h), 1e-6)
        wait_h = max(0.0, (now - one.since).total_seconds() / 3600)
        ranked.append(Ranked(one, worth, cost, weights.get(one.repo, 1.0) * worth / cost, wait_h))
    return tuple(sorted(ranked, key=lambda item: (-item.score, item.candidate.since, item.candidate.key)))


def starved(ranked: Sequence[Ranked], schedules: Mapping[str, Schedule]) -> tuple[Ranked, ...]:
    """The blocked candidates that waited past their class's ``starve_after_hours``."""
    found = []
    for item in ranked:
        bound = schedules[item.candidate.repo].starve_after_hours.get(item.candidate.kind)
        if item.blocked and bound is not None and item.wait_h > bound:
            found.append(item)
    return tuple(found)
