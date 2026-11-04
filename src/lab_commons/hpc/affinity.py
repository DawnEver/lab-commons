"""A RESEARCH submission never lands on a verify account (user ruling 2026-10-30).

HPC accounts have a default affinity: the VERIFY accounts produce the forge's verify parts, the others carry
research, and BY DEFAULT NEITHER BORROWS FROM THE OTHER. A job-file submission
(``python -m lab_commons.hpc plan|submit|retry -c <job.toml>``) is research, so :func:`place` spreads it over the
non-verify accounts only. Work they cannot take now WAITS there (a plan with ``room`` 0); with no research account
at all it is refused for room (:class:`~lab_commons.hpc.plan.Unplaceable`) and a later submit tries again.

THE ONE WAY TO BORROW is the user's explicit opt-in: :data:`lab_commons.hpc.allocation.RESEARCH_BORROWS_VERIFY` in
the expiring allocation record (``python -m dev_lab.dev.hpchost allocate --until <iso> --borrow research->verify``).
While it is unexpired the verify accounts are candidates like any other; expired or absent, they are not.

A forge verify run (``run submit``) is never placed here: an affined seat submits its own parts under its own login.

THE AFFINITY IS READ, NEVER DECLARED HERE. Each verify account declares itself once, on its own deploy row, and
publishes it on its forge host card; hpc never reaches the forge, so the forge installs the one reader as an entry
point in :data:`READER` (as :mod:`lab_commons.hpc.allocation` does). None installed, or a forge that cannot be read,
leaves the affinity unknown: placement is exactly what it was without one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import entry_points
from typing import Any, Final

from lab_commons.hpc import allocation
from lab_commons.hpc.grants import Affinity, Grant, Machine
from lab_commons.hpc.plan import Unplaceable
from lab_commons.hpc.span import Placement

__all__ = ['READER', 'declared', 'place', 'resolve']

#: The entry-point group the forge installs its affinity reader under: ``() -> Affinity | None``.
READER: Final = 'lab_commons.hpc.affinity'


def declared() -> Affinity | None:
    """The affinity through the installed :data:`READER`; ``None`` when none is installed or it knows none."""
    for point in entry_points(group=READER):
        return point.load()()
    return None


def resolve(
    machine: Machine,
    read: Callable[[], Affinity | None] | None = None,
    record: Callable[[], bytes | None] | None = None,
) -> Machine:
    """*machine* with the affinity *read* returns (default :func:`declared`), its borrow from the allocation *record*.

    *record* defaults to :func:`lab_commons.hpc.allocation.recorded`. An unreadable forge leaves no affinity; an
    unreadable record borrows nothing.
    """
    try:
        held = (read or declared)()
    except (OSError, ValueError):
        held = None
    if held is None:
        return replace(machine, affinity=None)
    try:
        raw = (record or allocation.recorded)()
        borrow = allocation.RESEARCH_BORROWS_VERIFY in allocation.borrowing(
            None if raw is None else raw.decode('utf-8'), now=datetime.now(UTC)
        )
    except (OSError, ValueError):
        borrow = False
    return replace(machine, affinity=replace(held, borrow=borrow))


def place(
    candidates: Sequence[tuple[Grant, Any]],
    held: Affinity | None,
    spread: Callable[[list[tuple[Grant, Any]]], tuple[Placement, ...]],
) -> tuple[Placement, ...]:
    """Where a research batch runs: *spread* over the non-verify *candidates* -- see the module docstring.

    Without *held*, with the user's borrow opt-in, or with no verify account among *candidates*, it is *spread*
    over them all, unchanged.
    """
    if held is None or held.borrow or not any(grant.user in held.verify for grant, _ in candidates):
        return spread(list(candidates))
    own = [(g, s) for g, s in candidates if g.user not in held.verify]
    if not own:
        kept = sorted(g.name for g, _ in candidates)
        msg = (
            f'no research account to wait on; the verify accounts {kept} never take research unless the user opts in '
            f'(allocate --borrow {allocation.RESEARCH_BORROWS_VERIFY})'
        )
        raise Unplaceable(msg)
    return spread(own)
