"""The box: ONE resource manager per machine.

Every run declares its :class:`Demand` (cores, memory, wall time, tool seats); :func:`admit` books
it in the machine's one ledger only when the declared sum fits the machine's :class:`Capacity`,
and a waiter that cannot fit holds nothing and gives up at its own limit. See
:mod:`lab_commons.box.ledger` for why a claim cannot be idle-but-held and why every wait ends.
"""

from __future__ import annotations

from lab_commons.box.demand import GB, Capacity, Demand, Holding, Refused, fits
from lab_commons.box.ledger import BOX_ENV, GRACE_S, POLL_S, Claim, admit, box_path, holdings

__all__ = [
    'BOX_ENV',
    'GB',
    'GRACE_S',
    'POLL_S',
    'Capacity',
    'Claim',
    'Demand',
    'Holding',
    'Refused',
    'admit',
    'box_path',
    'fits',
    'holdings',
]
