"""THE RULE -- who fits on a scope now. PURE.

A job is admitted when every resource it needs is present on the scope and its demand fits the scope's
supply less what already runs there. Nothing is reserved, lent or preempted (user ruling 2026-10-14):
what a box has is what its ledger can still admit (:mod:`lab_commons.box`), and priority is ordering only
(:mod:`lab_commons.compute.priority`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from lab_commons.compute.model import Job, Resources, Supply, total

#: Demands a supply meters only when it lists them present: a box, or a fixture, that states no ``files``
#: refuses nothing on files, so the file prior never makes its parts unclaimable (2026-10-14).
STATED_ONLY: Final = frozenset({'files'})

__all__ = ['STATED_ONLY', 'Admission', 'admit', 'claimable']


@dataclass(frozen=True)
class Admission:
    """The verdict of :func:`admit`: admitted, or the resource name(s) lacking -- never a bare False."""

    admitted: bool
    lacking: tuple[str, ...] = ()
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


def admit(
    job: Job, demand: Mapping[str, float], supply: Supply, *, running: Iterable[Mapping[str, float]] = ()
) -> Admission:
    """THE RULE: needs present; demand within supply less what runs.

    A missing tool is named before any shortfall: no amount of free cpu makes FEMM installed.
    """
    missing = tuple(sorted(job.needs - supply.present))
    if missing:
        return Admission(admitted=False, lacking=missing)
    free = supply.resources - total(running)
    # a quantity metered only where a supply STATES it: a supply that names no ``files`` refuses nothing on files
    want = Resources({k: v for k, v in demand.items() if k not in STATED_ONLY or k in supply.present})
    if want <= free:
        return Admission(admitted=True)
    short = want.short_of(free)
    return Admission(admitted=False, lacking=short, numbers=tuple((n, want[n], free[n]) for n in short))


def claimable(
    ranked: Sequence[tuple[Job, Mapping[str, float]]],
    supply: Supply,
    *,
    running: Sequence[Mapping[str, float]] = (),
) -> list[tuple[Job, Admission]]:
    """The jobs to claim and how each is admitted, walking *ranked* in its order and adding each claim to what runs.

    RANK ORDER IS AUTHORITATIVE, AND A NON-FIT DOES NOT STOP THE WALK: a job that does not fit is
    skipped and lower-ranked jobs that do fit are still claimed, so a large job never idles a host
    that has room for smaller ones. (Its starvation, if it lasts, is named by what it lacks.)
    """
    taken: list[Mapping[str, float]] = list(running)
    claimed = []
    for job, demand in ranked:
        verdict = admit(job, demand, supply, running=taken)
        if verdict:
            claimed.append((job, verdict))
            taken.append(Resources(demand))
    return claimed
