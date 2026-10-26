"""THE RULE -- who fits on a scope now. PURE.

A job is admitted when every resource it needs is present on the scope and its demand fits the scope's
supply less what already runs there. Nothing is reserved, lent or preempted (user ruling 2026-10-14):
what a box has is what its ledger can still admit (:mod:`lab_commons.box`), and priority is ordering only
(:mod:`lab_commons.compute.priority`). A name is metered exactly where a provider on the scope supplies it: a
demand naming one the supply does not count finds 0 of it and is refused, naming it. Nobody calls :func:`admit` but
the engine (:func:`lab_commons.resources.engine.admits`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from lab_commons.compute.model import Job, Resources, Supply, total

__all__ = ['Admission', 'admit']


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
    want = Resources(demand)
    if want <= free:
        return Admission(admitted=True)
    short = want.short_of(free)
    return Admission(admitted=False, lacking=short, numbers=tuple((n, want[n], free[n]) for n in short))
