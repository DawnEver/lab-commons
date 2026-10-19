"""The box: ONE resource manager per machine.

Every run declares its :class:`Demand` (cores, memory, wall time, tool seats); :func:`hold` books
it in the machine's one ledger only when the one rule (:func:`lab_commons.compute.admission.admit`)
admits it on the MEASURED room (:func:`lab_commons.box.supply.room`) within the machine's :class:`Capacity`,
and a waiter that cannot fit holds nothing and gives up at its own limit. See
:mod:`lab_commons.box.ledger` for why a claim cannot be idle-but-held and why every wait ends, and
:func:`run` (:mod:`lab_commons.box.run`) for how an admitted run is held to exactly its claim.
"""

from __future__ import annotations

from lab_commons.box.demand import (
    GB,
    NOT_ADMITTED,
    WALL_CEILING_S,
    Capacity,
    Claim,
    Demand,
    DuplicateKey,
    Holding,
    NotAdmitted,
    ProcessEnd,
    Refused,
    declared_seats,
    not_admitted,
    read_ended,
    seat_supply,
    write_ended,
)
from lab_commons.box.ledger import (
    BOX_ENV,
    CPU_ENV,
    GRACE_S,
    INSIDE_ENV,
    POLL_S,
    adopted,
    box_path,
    hand_over,
    hold,
    holdings,
    persist,
    release,
    take,
)
from lab_commons.box.run import command, run

__all__ = [
    'BOX_ENV',
    'CPU_ENV',
    'GB',
    'GRACE_S',
    'INSIDE_ENV',
    'NOT_ADMITTED',
    'POLL_S',
    'WALL_CEILING_S',
    'Capacity',
    'Claim',
    'Demand',
    'DuplicateKey',
    'Holding',
    'NotAdmitted',
    'ProcessEnd',
    'Refused',
    'adopted',
    'box_path',
    'command',
    'declared_seats',
    'hand_over',
    'hold',
    'holdings',
    'not_admitted',
    'persist',
    'read_ended',
    'release',
    'run',
    'seat_supply',
    'take',
    'write_ended',
]
