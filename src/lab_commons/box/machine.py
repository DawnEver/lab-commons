"""The machine file's resource facts: the ``[reserve]`` margins and the ``[capabilities]`` seats.

ONE HOME for each reader. The box is the lowest layer of resource truth, so the facts every other
layer reads about this machine's resources are parsed here and imported upward -- by the box's own
:class:`~lab_commons.box.demand.Capacity` and through it the forge's box supply -- and
nowhere else. Each reader validates its table and refuses a malformed row by name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    'DEFAULT_CPU_MARGIN',
    'DEFAULT_MEM_MARGIN',
    'RESOURCE_NAME',
    'ReserveTable',
    'own_reserve',
    'provided',
    'reserve_table',
]

#: Cores kept free above what the box's own user is using now.
DEFAULT_CPU_MARGIN: Final = 1.0

#: The fraction of the box's memory kept free above what its own user is using now.
DEFAULT_MEM_MARGIN: Final = 0.25

#: The spelling of one resource name: a ``[capabilities.<name>]`` table, a need-set marker.
RESOURCE_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*$')

_WHERE: Final = 'the machine file'


@dataclass(frozen=True)
class ReserveTable:
    """The machine file's ``[reserve]``: the margins this box keeps for its own user."""

    cpu_margin: float = DEFAULT_CPU_MARGIN
    mem_margin: float = DEFAULT_MEM_MARGIN


def reserve_table(table: Mapping[str, Any]) -> ReserveTable:
    """Read ``[reserve]``; an unknown key or a margin out of range is refused."""
    unknown = sorted(set(table) - set(ReserveTable.__dataclass_fields__))
    cpu = table.get('cpu_margin', DEFAULT_CPU_MARGIN)
    mem = table.get('mem_margin', DEFAULT_MEM_MARGIN)
    bad = [
        *(f'unknown key {key!r}' for key in unknown),
        *(['cpu_margin must be >= 0 cores'] if not isinstance(cpu, int | float) or cpu < 0 else []),
        *(['mem_margin must be a fraction in [0, 1]'] if not isinstance(mem, int | float) or not 0 <= mem <= 1 else []),
    ]
    if bad:
        msg = f'{_WHERE} [reserve]: {"; ".join(bad)}'
        raise ValueError(msg)
    return ReserveTable(float(cpu), float(mem))


def own_reserve(total: Mapping[str, float], in_use: Mapping[str, float], table: ReserveTable) -> dict[str, float]:
    """What this box keeps for its own user: ``{'cpu', 'mem_gb'}`` in use now plus the margins, capped at the box."""
    wanted = {
        'cpu': in_use.get('cpu', 0.0) + table.cpu_margin,
        'mem_gb': in_use.get('mem_gb', 0.0) + table.mem_margin * total.get('mem_gb', 0.0),
    }
    return {name: min(amount, total.get(name, 0.0)) for name, amount in wanted.items()}


def provided(table: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The machine file's ``[capabilities]``: ``{name: facts}``; a malformed row is refused by name."""
    out: dict[str, dict[str, Any]] = {}
    for name, facts in table.items():
        seats = facts.get('seats', 1) if isinstance(facts, dict) else None
        if not RESOURCE_NAME.match(name) or not isinstance(seats, int) or isinstance(seats, bool) or seats < 1:
            msg = f'{_WHERE}: [capabilities.{name}] must be a table named {RESOURCE_NAME.pattern}, seats a positive int'
            raise ValueError(msg)
        out[name] = dict(facts)
    return out
