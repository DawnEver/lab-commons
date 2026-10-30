"""A RESEARCH submission yields the verify accounts (user ruling 2026-10-30, D103).

HPC accounts have a default affinity: the VERIFY accounts produce the forge's verify parts first, the others carry
research. A job-file submission (``python -m lab_commons.hpc plan|submit|retry -c <job.toml>``) is research, so
:func:`place`:

* places it on the non-verify accounts, as it would without an affinity;
* borrows a verify account only when those cannot take it with room and the verify account is IDLE -- no verify
  part running or claimable for it (:attr:`Affinity.busy`) -- and then asks Slurm for :data:`NICE` there, so a
  verify job queued later starts first. Nothing is ever cancelled or reserved;
* never borrows a busy verify account: the work waits on a research account (a plan with ``room`` 0), or, with
  none to wait on, is refused for room (:class:`~lab_commons.hpc.plan.Unplaceable`) and a later submit tries again.

A forge verify run (``run submit``) is never placed here: an affined seat submits its own parts under its own login.

THE AFFINITY IS READ, NEVER DECLARED HERE. Each verify account declares itself once, on its own deploy row, and
publishes it on its forge host card; hpc never reaches the forge, so the forge installs the one reader as an entry
point in :data:`READER` (as :mod:`lab_commons.hpc.allocation` does). None installed, or a forge that cannot be read,
leaves the affinity unknown: placement is exactly what it was without one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from importlib.metadata import entry_points
from typing import Any, Final

from lab_commons.hpc.grants import Affinity, Grant, Machine
from lab_commons.hpc.plan import Unplaceable
from lab_commons.hpc.span import Placement

__all__ = ['NICE', 'READER', 'declared', 'place', 'resolve']

#: The entry-point group the forge installs its affinity reader under: ``() -> Affinity | None``.
READER: Final = 'lab_commons.hpc.affinity'

#: Slurm ``--nice`` of a research array on a borrowed verify account: later verify jobs start first.
NICE: Final = 1000


def declared() -> Affinity | None:
    """The affinity through the installed :data:`READER`; ``None`` when none is installed or it knows none."""
    for point in entry_points(group=READER):
        return point.load()()
    return None


def resolve(machine: Machine, read: Callable[[], Affinity | None] | None = None) -> Machine:
    """*machine* with the affinity *read* returns (default :func:`declared`); an unreadable forge leaves none."""
    try:
        held = (read or declared)()
    except (OSError, ValueError):
        held = None
    return replace(machine, affinity=held)


def place(
    candidates: Sequence[tuple[Grant, Any]],
    held: Affinity | None,
    spread: Callable[[list[tuple[Grant, Any]]], tuple[Placement, ...]],
) -> tuple[Placement, ...]:
    """Where a research batch runs: *spread* over the non-verify *candidates*, borrowing an idle verify one niced.

    Without *held*, or with no verify account among *candidates*, it is *spread* over them all, unchanged.
    """
    if held is None or not any(grant.user in held.verify for grant, _ in candidates):
        return spread(list(candidates))
    own = [(g, s) for g, s in candidates if g.user not in held.verify]
    idle = [(g, s) for g, s in candidates if g.user in held.verify and g.user not in held.busy]
    refused: Unplaceable | None = None
    if own:
        try:
            placed = spread(own)
        except Unplaceable as no_room:
            refused = no_room
        else:
            if not idle or all(p.plan.room != 0 for p in placed):
                return placed
    if not idle:
        busy = sorted(g.name for g, _ in candidates if g.user in held.busy)
        msg = f'{refused or "no research account"}; every busy verify account is kept for its verify parts: {busy}'
        raise Unplaceable(msg)
    return tuple(
        replace(p, plan=replace(p.plan, nice=NICE)) if p.grant.user in held.verify else p for p in spread([*own, *idle])
    )
