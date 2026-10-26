"""THE MODEL -- four nouns and one rule, PURE: no I/O, and nothing imported from ``dev`` or ``hpc``.

**Resources** are named, countable, non-negative quantities, and ALL ARE EQUAL: ``cpu``, ``mem_gb``,
``jmag``, ``femm``, ``windows``... A name a vector omits is zero. A host's **Supply** is its measured
vector plus the names PRESENT on it (a tool installed with every seat in use is present at 0). A
**Job** is ``(repo, sha, producer, needs)``; its **Demand** is its
size. Nothing is reserved, lent or preempted (user ruling 2026-10-14): a host's supply is what its
box ledger can still admit (:mod:`lab_commons.box`), and priority is ordering only.

THE RULE, for every host alike::

    admit(job) = needs(job) <= present(host) and demand(job) <= supply(host) - running(host)

:func:`rank` decides ORDER (value per cost, aging, boosts); :mod:`lab_commons.compute.admission` holds the
RULE -- :func:`~lab_commons.compute.admission.admit` decides FIT, ``claimable`` walks the order and claims what fits.

THE NUMBERS OF THE ORDER ARE DATA, declared once in ``[tool.lab_commons.schedule]`` and loaded by
:func:`dev_lab.dev.forgeschedule.load_schedule` into a :class:`Schedule`::

    value = base[class] + boost + aging_rate * wait_hours
    score = weight[repo] * value / expected_cost
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

__all__ = [
    'BRANCH',
    'FEATURE',
    'INTEGRATION',
    'PR',
    'REQUEST',
    'Candidate',
    'Demand',
    'Job',
    'Ranked',
    'Resources',
    'Schedule',
    'Supply',
    'boost_of',
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
    listed: frozenset[str] = frozenset()

    @property
    def present(self) -> frozenset[str]:
        """The names this host has: those *listed* (a tool can be present at 0), else the names it counts."""
        return self.listed or self.resources.names

    def __post_init__(self) -> None:
        """Refuse an inconsistent value at construction."""
        absent = sorted(self.resources.names - self.present)
        if absent:
            msg = f'{self.scope} counts {absent} it does not have present'
            raise ValueError(msg)


# --- jobs -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One unit of work: *producer* run at *sha* of *repo*, needing the resource names *needs*."""

    repo: str
    sha: str
    producer: str
    needs: frozenset[str] = frozenset()


# --- the order ------------------------------------------------------------------------------------------

#: The class of each candidate source the family knows; a repository may add classes of its own.
INTEGRATION: Final = 'integration'
PR: Final = 'pr'
FEATURE: Final = 'feature'
BRANCH: Final = 'branch'
REQUEST: Final = 'request'

_PRIORITY: Final = re.compile(r'^priority:(?P<n>-?\d+(?:\.\d+)?)$')

#: Where the schedule's numbers are declared, for a refusal to name.
SCHEDULE_TABLE: Final = 'schedule'


@dataclass(frozen=True)
class Schedule:
    """``[tool.lab_commons.schedule]``: the family default; a repository overrides key by key."""

    classes: Mapping[str, float] = field(
        default_factory=lambda: {INTEGRATION: 100.0, PR: 60.0, FEATURE: 45.0, BRANCH: 30.0, REQUEST: 20.0}
    )
    #: Value gained per hour a candidate waits -- the term that guarantees no class starves forever.
    aging_rate: float = 10.0
    starve_after_hours: Mapping[str, float] = field(
        default_factory=lambda: {INTEGRATION: 6.0, PR: 12.0, FEATURE: 18.0, BRANCH: 24.0, REQUEST: 48.0}
    )
    #: Core-hours one ref of the class may use per 24 h; a class absent here is unbounded.
    daily_budget_core_h: Mapping[str, float] = field(default_factory=dict)
    #: Label -> value added; ``priority:<n>`` adds ``n`` without being declared.
    boosts: Mapping[str, float] = field(default_factory=lambda: {'priority:user': 1000.0})
    #: THE ONE RULE naming a child branch's class: ordered ``fnmatch`` pattern -> class, first match wins.
    #: A ref no pattern matches is not a child and is not served (a PR into integration still is, as ``pr``).
    #: A declared table REPLACES this default whole: an ordered rule is not merged key by key.
    classes_by_ref: Mapping[str, str] = field(
        default_factory=lambda: {'feat/*': FEATURE, 'fix/*': FEATURE, 'lane/*': BRANCH}
    )
    #: Classes whose ref is COALESCED (D6, user ruling 2026-10-17): at most one run per (ref, part) in flight
    #: across the fleet, and the ref's current tip waits behind it -- the next tip is tested as one batch. A
    #: class absent here (a lane ``branch``) is tested at every pushed sha. A ``pr`` head is coalesced: one PR
    #: session holds at most one run per part in flight (user ruling 2026-10-22).
    coalesce: tuple[str, ...] = (INTEGRATION, FEATURE, PR)
    #: Classes served STRICTLY before every other, in this order, whatever their score (user ruling
    #: 2026-10-22: on a scarce host the integration branch, then the PRs into it, never pile up behind lanes).
    served_first: tuple[str, ...] = (INTEGRATION, PR)
    #: Classes ordered OLDEST FIRST (by when their sha was committed) instead of by score.
    oldest_first: tuple[str, ...] = (PR,)
    #: The label that makes an open issue a request.
    request_label: str = 'forge:request'
    #: Hours a request may wait unclaimed before its issue is told, ONCE, why the host that saw it did not admit it.
    request_unadmitted_hours: float = 6.0
    #: The expected cost, in hours, of work nothing has measured yet.
    prior_cost_h: float = 4.0

    def base(self, kind: str) -> float:
        """The base value of class *kind*; an undeclared class is refused by name."""
        if kind not in self.classes:
            msg = f'class {kind!r} is not in [tool.lab_commons.{SCHEDULE_TABLE}] classes {sorted(self.classes)}'
            raise ValueError(msg)
        return float(self.classes[kind])

    def class_of(self, ref: str) -> str | None:
        """The class :attr:`classes_by_ref` gives child branch *ref* (first match); ``None``: not a child."""
        return next((kind for one, kind in self.classes_by_ref.items() if fnmatch.fnmatchcase(ref, one)), None)


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
    origin: str = ''  # the forge login of its PR, request or head commit; '' when unresolved
    origin_host: str = ''  # the host card it says created it; kept only where its origin login vouches for it
    session: str = ''  # the ref it was pushed to (one lane is one session); '' for a request
    producer: str = ''  # a request's declared producer name (dev_lab.dev.forgerequest); '' for a part
    refused: str = ''  # why its own request body is refused (an unvouched host pin); '' when it is not
    paths: tuple[str, ...] = ()  # the test files a TESTS request names (dev_lab.dev.forgerequest); () else

    def __post_init__(self) -> None:
        """Work is always AT a commit: an empty sha is refused here, never carried to a ``statuses/<sha>`` URL."""
        if not self.sha:
            msg = f'{self.ref} names no commit sha: a candidate is work at a commit'
            raise ValueError(msg)

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
    priority: bool = False  # the ranking host's [priority] names its subject: it precedes every other


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
    priority: Callable[[Candidate], bool] = lambda _: False,
) -> tuple[Ranked, ...]:
    """Every candidate ordered: *priority*, a user boost, ``served_first`` classes in order, then the rest.

    Within a class by score, or oldest first for an ``oldest_first`` class (user ruling 2026-10-22).

    *priority* is the ranking host's declared ordering (:mod:`lab_commons.compute.priority`): pure order,
    never a claim on supply.

    PURE: the one ordering rule. *schedules* and *cost_h* are keyed by repo; *weights* by repo, 1 for a
    repo it omits. A host's own user is served by its own reserve (:mod:`lab_commons.box.machine`), never by a lift.
    """
    weights = weights or {}
    ranked = []
    for one in candidates:
        schedule = schedules[one.repo]
        worth = value(one, schedule, now)
        cost = max(cost_h.get(one.repo, schedule.prior_cost_h), 1e-6)
        wait_h = max(0.0, (now - one.since).total_seconds() / 3600)
        score = weights.get(one.repo, 1.0) * worth / cost
        ranked.append(Ranked(one, worth, cost, score, wait_h, priority=priority(one)))
    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                not item.priority,
                boost_of(item.candidate.labels, schedules[item.candidate.repo]) <= 0,
                *_tier(item.candidate, schedules[item.candidate.repo], item.score),
                item.candidate.since,
                item.candidate.key,
            ),
        )
    )


def _tier(candidate: Candidate, schedule: Schedule, score: float) -> tuple[int, float]:
    """``(strict tier, order within it)``: a ``served_first`` class's index (the rest after), then score or age."""
    first = schedule.served_first
    tier = first.index(candidate.kind) if candidate.kind in first else len(first)
    return tier, candidate.since.timestamp() if candidate.kind in schedule.oldest_first else -score


def starved(ranked: Sequence[Ranked], schedules: Mapping[str, Schedule]) -> tuple[Ranked, ...]:
    """The blocked candidates that waited past their class's ``starve_after_hours``."""
    found = []
    for item in ranked:
        bound = schedules[item.candidate.repo].starve_after_hours.get(item.candidate.kind)
        if item.blocked and bound is not None and item.wait_h > bound:
            found.append(item)
    return tuple(found)
