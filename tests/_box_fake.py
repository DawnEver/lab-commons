"""A fake box for the box and compute tests.

Here only :func:`quiet`, the OS reader of an otherwise idle box, is shared by the box and compute tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from lab_commons.box import Capacity
from lab_commons.box.supply import Reading


def quiet(capacity: Capacity) -> Callable[[Iterable[int]], Reading]:
    """An OS reader of a box nobody but its claims uses: idle and available are its whole size and margins.

    On it the measured room equals the declared one (*capacity* less the claims), so a test that hands the
    ledger a size by hand is not decided by how loaded the machine running it happens to be.
    """
    reading = Reading(
        cores=capacity.cpu + capacity.kept_cpu,
        idle_cores=capacity.cpu + capacity.kept_cpu,
        total_bytes=capacity.mem_bytes + capacity.kept_bytes,
        available_bytes=capacity.mem_bytes + capacity.kept_bytes,
    )
    return lambda _pids: reading
