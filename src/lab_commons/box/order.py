"""The queue's ONE ordering rule: a ticket is admitted past an earlier one only if that does not delay its start.

FIFO admitted nothing past an earlier ticket that could not fit, so a big demand waiting on a long claim
idled every small one behind it for hours (live 2026-10-23). The rule is BACKFILL BY RESERVATION, and it
needs nothing the ledger lacks, because every claim carries its deadline:

- an earlier contending ticket's EARLIEST START (:func:`earliest`) is the first moment -- now, or a held
  claim's deadline -- at which the room, with every claim ending by then released, admits it;
- a later ticket that fits now is admitted iff, held as a claim to its own deadline, it moves NO earlier
  contending ticket's earliest start later (:func:`blocks`).

WHY IT CANNOT STARVE: an admission after a ticket was written never moves that ticket's earliest start
later, and that start is a held claim's deadline -- each ends by then (its wall, at most
:data:`~lab_commons.box.demand.WALL_CEILING_S`). So a ticket waits at most until the claims held when it
arrived end, however many smaller ones pass it. Two exceptions, both named: room the OS uses outside the
ledger is measured, not scheduled (a ticket that cannot fit even with every claim released has no start to
protect, and blocks nobody until it has one); and a HOLDER is never ordered behind a waiter.

A HOLDER NEVER WAITS BEHIND A WAITER (the 2026-10-19 deadlock): a process holding a claim whose nested
request queued behind a waiter needing that claim's memory waited until its own deadline. A ticket of a
process that holds a claim is ordered by nothing -- it is admitted as soon as it fits. That passing is
bounded too: a holder stops being one when its claims end, and no new holder appears but by this rule.

Contention is per resource (:func:`contends`): a ticket wanting nothing an earlier one wants -- a seats-only
check, another seat -- is never ordered behind it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from lab_commons.box.demand import GB, Capacity, Holding
from lab_commons.box.demand import seats as seats_of
from lab_commons.box.providers import measured
from lab_commons.box.supply import Reading, Usage
from lab_commons.compute.model import Demand, Resources
from lab_commons.resources.engine import admits

__all__ = ['Asked', 'blocks', 'contends', 'earliest', 'fits']

#: An earlier ticket's ask as its row stores it: cores, memory bytes, the seats JSON of every other amount by name;
#: NULL on a pre-demand row.
Asked = tuple[int | None, int | None, str | None]


def _vector(cpu: float, mem_bytes: int, seats: dict[str, int]) -> Resources:
    """The admission vector of a demand or a claim."""
    return Resources({'cpu': cpu, 'mem_gb': mem_bytes / GB, **seats})


def fits(capacity: Capacity, held: Sequence[Holding], reading: Reading, demand: Demand) -> bool:
    """Whether the engine's rule admits *demand* on what the box measured in *reading* less *held*."""
    supply = measured(capacity, held, reading, seats=seats_of(demand))
    running = [_vector(h.cpu, h.mem_bytes, dict(h.seats)) for h in held]
    return bool(admits(supply.resources, demand, present=supply.present, running=running))


def contends(holder: Holding, ahead: Asked) -> bool:
    """Whether an earlier ticket asking *ahead* wants any resource *holder* holds; an undeclared one wants all."""
    cpu, mem_bytes, seats = ahead
    if cpu is None or mem_bytes is None or seats is None:
        return True
    wanted = json.loads(seats)
    return bool(
        (cpu and holder.cpu)
        or (mem_bytes and holder.mem_bytes)
        or any(amount and wanted.get(name) for name, amount in holder.seats.items())
    )


def _released(reading: Reading, gone: Sequence[Holding]) -> Reading:
    """*reading* once *gone* ended: what their trees use is free again."""
    used = [reading.usage.get(h.pid, Usage(0, 0.0)) for h in gone]
    return replace(
        reading,
        idle_cores=reading.idle_cores + sum(u.busy_cores for u in used),
        available_bytes=reading.available_bytes + sum(u.resident_bytes for u in used),
    )


def earliest(capacity: Capacity, held: Sequence[Holding], reading: Reading, demand: Demand, now: float) -> float:
    """The first moment -- *now* or a deadline in *held* -- *demand* fits once every claim ending by then left.

    ``inf`` when it fits not even on an empty ledger: what keeps it out is used outside the ledger.
    """
    if fits(capacity, held, reading, demand):
        return now
    for moment in sorted({h.deadline for h in held}):
        gone = [h for h in held if h.deadline <= moment]
        stay = [h for h in held if h.deadline > moment]
        if fits(capacity, stay, _released(reading, gone), demand):
            return moment
    return float('inf')


def blocks(
    asked: Asked, jumper: Holding, *, capacity: Capacity, held: Sequence[Holding], reading: Reading, now: float
) -> bool:
    """Whether an earlier ticket asking *asked* orders *jumper* behind it: it contends and *jumper* delays its start.

    A ticket that declares no demand has no start to compute: it orders everything behind it.
    """
    if not contends(jumper, asked):
        return False
    cpu, mem_bytes, seats = asked
    if cpu is None or mem_bytes is None or seats is None:
        return True
    ahead = Demand(_vector(cpu, mem_bytes, json.loads(seats)))
    before = earliest(capacity, held, reading, ahead, now)
    return earliest(capacity, [*held, jumper], reading, ahead, now) > before
