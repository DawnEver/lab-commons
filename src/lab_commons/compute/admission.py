"""THE RULE -- who fits on a scope now, and what a reservation LENDS (user ruling 2026-10-13). PURE.

A reservation (:class:`~lab_commons.compute.model.Reservation`) holds some resources of one scope, or all
of it. What its holders leave IDLE is lent: a job it is not for may be admitted ON LOAN into it, so nothing
idles. A holder's job that does not fit because of loans PREEMPTS them -- newest first, only as many as
needed -- and a holder with room elsewhere preempts nobody. What runs is a sequence of :class:`Held`, each
naming the lease it borrows from; the serve loop stops and releases what :func:`admit` says to preempt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from lab_commons.compute.model import Job, Reservation, Resources, Supply, total

__all__ = ['Admission', 'Held', 'admit', 'claimable', 'idle', 'preempt']


# --- the rule -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Held:
    """One thing running on a scope: its *demand*, its *job* (``None``: not a forge job), since when.

    *on_loan* is the id of the reservation it borrows from (0: none) -- a holder of that lease may PREEMPT it.
    """

    demand: Resources
    job: Job | None = None
    on_loan: int = 0
    since: datetime | None = None

    @property
    def label(self) -> str:
        """``<sha12> <producer>``; ``-`` for a run that is not a forge job."""
        return f'{self.job.sha[:12]} {self.job.producer}' if self.job else '-'


# --- the rule -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    """The verdict of :func:`admit`: admitted, or the resource name(s) lacking -- never a bare False.

    Admitted *on_loan* from a reservation's unused part (its id), or by PREEMPTING the loans *preempt*.
    """

    admitted: bool
    lacking: tuple[str, ...] = ()
    on_loan: int = 0
    preempt: tuple[Held, ...] = ()
    numbers: tuple[tuple[str, float, float], ...] = field(default=(), compare=False)  # (name, asked, free)

    def __bool__(self) -> bool:
        """Truthy exactly when admitted."""
        return self.admitted

    @property
    def why(self) -> str:
        """``''`` when admitted, else which resources are lacking -- with asked and free for an amount."""
        if self.admitted:
            return ''
        said = {name: f'{name} ({asked:g} asked, {free:g} free)' for name, asked, free in self.numbers}
        return f'lacking {", ".join(said.get(name, name) for name in self.lacking)}'


def idle(lease: Reservation, supply: Supply, running: Iterable[Held]) -> Resources:
    """What *lease* holds that nobody uses: its amount less its holders' jobs and its loans running."""
    used = total(
        one.demand
        for one in running
        if (one.on_loan and one.on_loan == lease.id)
        or (not one.on_loan and one.job is not None and lease.for_.matches(one.job))
    )
    return lease.amount(supply) - used


def preempt(
    demand: Resources, free: Resources, running: Iterable[Held], leases: Iterable[int]
) -> tuple[Held, ...] | None:
    """The loans in *leases* to stop so *demand* fits: NEWEST FIRST, only as many as needed; ``None`` when none do.

    Nothing (``()``) when it fits *free* already: a holder with room elsewhere preempts nobody.
    """
    ids = set(leases)
    loans = sorted((one for one in running if one.on_loan and one.on_loan in ids), key=_newest_first)
    picked: list[Held] = []
    for one in loans:
        if demand <= free + total(held.demand for held in picked):
            break
        picked.append(one)
    return tuple(picked) if demand <= free + total(held.demand for held in picked) else None


def _newest_first(one: Held) -> float:
    return -(one.since.timestamp() if one.since else 0.0)


def admit(
    job: Job,
    demand: Mapping[str, float],
    supply: Supply,
    *,
    reservations: Iterable[Reservation] = (),
    running: Iterable[Held] = (),
    now: datetime,
) -> Admission:
    """THE RULE: needs present; demand within supply less what runs and the IDLE part of others' live leases.

    A missing tool is named before any shortfall: no amount of free cpu makes FEMM installed. Failing that:

    * a HOLDER (a live lease here is for the job) may PREEMPT the loans in its leases (:func:`preempt`);
    * anyone else may BORROW one lease's idle part, admitted ``on_loan`` from it -- so nothing idles.
    """
    missing = tuple(sorted(job.needs - supply.present))
    if missing:
        return Admission(admitted=False, lacking=missing)
    held = tuple(running)
    live = [one for one in reservations if one.live_on(supply.scope, now)]
    mine = [one.id for one in live if one.for_.matches(job)]
    others = [one for one in live if not one.for_.matches(job)]
    free = supply.resources - total(one.demand for one in held) - total(idle(one, supply, held) for one in others)
    want = Resources(demand)
    if want <= free:
        return Admission(admitted=True)
    if mine:
        victims = preempt(want, free, held, mine)
        if victims is not None:
            return Admission(admitted=True, preempt=victims)
    for lease in others:
        if want <= free + idle(lease, supply, held):
            return Admission(admitted=True, on_loan=lease.id)
    short = want.short_of(free)
    return Admission(admitted=False, lacking=short, numbers=tuple((n, want[n], free[n]) for n in short))


def claimable(
    ranked: Sequence[tuple[Job, Mapping[str, float]]],
    supply: Supply,
    *,
    reservations: Sequence[Reservation] = (),
    running: Sequence[Held] = (),
    now: datetime,
) -> list[tuple[Job, Admission]]:
    """The jobs to claim and how each is admitted, walking *ranked* in its order and adding each claim to what runs.

    RANK ORDER IS AUTHORITATIVE, AND A NON-FIT DOES NOT STOP THE WALK: a job that does not fit is
    skipped and lower-ranked jobs that do fit are still claimed, so a large job never idles a host
    that has room for smaller ones. (Its starvation, if it lasts, is named by what it lacks.) A loan a
    claim preempts leaves what runs.
    """
    taken: list[Held] = list(running)
    claimed = []
    for job, demand in ranked:
        verdict = admit(job, demand, supply, reservations=reservations, running=taken, now=now)
        if verdict:
            claimed.append((job, verdict))
            taken = [one for one in taken if one not in verdict.preempt]
            taken.append(Held(Resources(demand), job, verdict.on_loan, now))
    return claimed
