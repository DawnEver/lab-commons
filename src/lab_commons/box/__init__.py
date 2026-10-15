"""The box: ONE resource manager per machine.

Every run declares its :class:`Demand` (cores, memory, wall time, tool seats); :func:`admit` books
it in the machine's one ledger only when the declared sum fits the machine's :class:`Capacity`,
and a waiter that cannot fit holds nothing and gives up at its own limit. See
:mod:`lab_commons.box.ledger` for why a claim cannot be idle-but-held and why every wait ends, and
:func:`run` (:mod:`lab_commons.box.run`) for how an admitted run is held to exactly its claim.
"""

from __future__ import annotations

from lab_commons.box.demand import (
    GB,
    Capacity,
    Demand,
    Holding,
    Outcome,
    Refused,
    declared_seats,
    fits,
    read_ended,
    write_ended,
)
from lab_commons.box.ledger import BOX_ENV, GRACE_S, POLL_S, Claim, admit, box_path, holdings
from lab_commons.box.run import command, run

__all__ = [
    'BOX_ENV',
    'GB',
    'GRACE_S',
    'POLL_S',
    'Capacity',
    'Claim',
    'Demand',
    'Holding',
    'Outcome',
    'Refused',
    'admit',
    'box_path',
    'command',
    'declared_seats',
    'fits',
    'holdings',
    'read_ended',
    'run',
    'write_ended',
]
