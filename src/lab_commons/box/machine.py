"""The machine file's resource facts: the ``[reserve]`` margins and the ``[capabilities]`` seats.

ONE HOME for each reader. The box is the lowest layer of resource truth, so the facts every other
layer reads about this machine's resources are parsed here and imported upward -- by the box's own
:class:`~lab_commons.box.demand.Capacity` and through it the forge's box supply -- and
nowhere else. Each reader validates its table and refuses a malformed row by name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    'DEFAULT_CPU_MARGIN',
    'DEFAULT_MEM_MARGIN',
    'RESOURCE_NAME',
    'ReserveTable',
    'own_reserve',
    'provided',
    'reserve_table',
    'watched',
]

#: Cores kept free above what the box's own user is using now (measured, :mod:`lab_commons.box.supply`).
DEFAULT_CPU_MARGIN: Final = 1.0

#: The fraction of the box's memory kept free above what its own user is using now.
DEFAULT_MEM_MARGIN: Final = 0.10

#: The spelling of one resource name: a ``[capabilities.<name>]`` table, a need-set marker.
RESOURCE_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*$')

_WHERE: Final = 'the machine file'


@dataclass(frozen=True)
class ReserveTable:
    """The machine file's ``[reserve]``: the margins this box keeps for its own user.

    ``seat_margin`` is ``{tool: seats}`` never lent, the same kind as ``cpu_margin``: a workstation's own user
    keeps them above what the OS shows in use (``seat_margin = { jmag = 2 }``).
    """

    cpu_margin: float = DEFAULT_CPU_MARGIN
    mem_margin: float = DEFAULT_MEM_MARGIN
    seat_margin: Mapping[str, int] = field(default_factory=dict)


def reserve_table(table: Mapping[str, Any]) -> ReserveTable:
    """Read ``[reserve]``; an unknown key or a margin out of range is refused."""
    unknown = sorted(set(table) - set(ReserveTable.__dataclass_fields__))
    cpu = table.get('cpu_margin', DEFAULT_CPU_MARGIN)
    mem = table.get('mem_margin', DEFAULT_MEM_MARGIN)
    seat = table.get('seat_margin', {})
    bad = [
        *(f'unknown key {key!r}' for key in unknown),
        *(['cpu_margin must be >= 0 cores'] if not isinstance(cpu, int | float) or cpu < 0 else []),
        *(['mem_margin must be a fraction in [0, 1]'] if not isinstance(mem, int | float) or not 0 <= mem <= 1 else []),
        *([] if _counts(seat) else [f'seat_margin must map {RESOURCE_NAME.pattern} to an int >= 0']),
    ]
    if bad:
        msg = f'{_WHERE} [reserve]: {"; ".join(bad)}'
        raise ValueError(msg)
    return ReserveTable(float(cpu), float(mem), dict(seat))


def _counts(table: object) -> bool:
    return isinstance(table, dict) and all(
        isinstance(name, str) and RESOURCE_NAME.match(name) and _whole(count) and count >= 0
        for name, count in table.items()
    )


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def own_reserve(total: Mapping[str, float], table: ReserveTable) -> dict[str, float]:
    """The margin this box keeps above what its own user uses: ``{'cpu', 'mem_gb'}``, capped at the box.

    What the user USES is not here: it is in the OS reading the box supply is measured from
    (:mod:`lab_commons.box.supply`), so it is counted once, as it happens, and never declared.
    """
    wanted = {'cpu': table.cpu_margin, 'mem_gb': table.mem_margin * total.get('mem_gb', 0.0)}
    return {name: min(amount, total.get(name, 0.0)) for name, amount in wanted.items()}


def provided(table: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The machine file's ``[capabilities]``: ``{name: facts}``; a malformed row is refused by name.

    ``seats`` is the DECLARED count (default 1); ``supply``, when given, is the argv of the probe that MEASURES it
    (:func:`lab_commons.box.demand.seat_supply`); ``in_use = { process = [images] }``, when given, names the process
    images whose running count is the seats in use NOW (:func:`watched`, :func:`lab_commons.box.supply.observe`).
    """
    out: dict[str, dict[str, Any]] = {}
    for name, facts in table.items():
        seats = facts.get('seats', 1) if isinstance(facts, dict) else None
        if not RESOURCE_NAME.match(name) or not isinstance(seats, int) or isinstance(seats, bool) or seats < 1:
            msg = f'{_WHERE}: [capabilities.{name}] must be a table named {RESOURCE_NAME.pattern}, seats a positive int'
            raise ValueError(msg)
        supply = facts.get('supply', [])
        if not isinstance(supply, list) or not all(isinstance(arg, str) for arg in supply):
            msg = f'{_WHERE}: [capabilities.{name}] supply must be an argv list of strings printing the seat count'
            raise ValueError(msg)
        in_use = facts.get('in_use', {'process': ['-']})
        images = in_use.get('process') if isinstance(in_use, dict) and set(in_use) == {'process'} else None
        if not isinstance(images, list) or not images or not all(isinstance(i, str) and i for i in images):
            msg = f'{_WHERE}: [capabilities.{name}] in_use must be {{ process = ["<image>.exe", ...] }}, non-empty'
            raise ValueError(msg)
        out[name] = dict(facts)
    return out


def watched(capabilities: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    """``{tool: images}`` for each :func:`provided` row declaring ``in_use``: the images, lower-cased."""
    return {
        name: tuple(image.lower() for image in facts['in_use']['process'])
        for name, facts in capabilities.items()
        if 'in_use' in facts
    }
