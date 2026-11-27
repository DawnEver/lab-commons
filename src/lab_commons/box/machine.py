"""The machine file's resource facts: the ``[reserve]`` margins and the ``[capabilities]`` seats.

ONE HOME for each reader. The box is the lowest layer of resource truth, so the facts every other
layer reads about this machine's resources are parsed here and imported upward -- by the box's own
:class:`~lab_commons.box.demand.Capacity` and through it the forge's box supply -- and
nowhere else. Each reader validates its table and refuses a malformed row by name.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.hpc._retired_compute import COMPUTE

__all__ = [
    'DEFAULT_CPU_MARGIN',
    'DEFAULT_CPU_MARGIN_SHARE',
    'DEFAULT_MEM_MARGIN',
    'NON_VENDOR_CHECKS',
    'RESOURCE_NAME',
    'UNLIMITED',
    'VENDOR_TOOLS',
    'ReserveTable',
    'UndeclaredSeat',
    'own_reserve',
    'provided',
    'reserve_table',
    'seat_count',
    'seat_size',
    'watched',
    'withheld',
]

#: Cores kept free above what the box's own user is using now (measured, :mod:`lab_commons.box.supply`).
DEFAULT_CPU_MARGIN: Final = 1.0

#: The most of the box's cores the cpu margin may keep: OS headroom scales with the host, so a 2-core box keeps
#: half a core, not half of itself -- a whole core there left an idle box unable to admit one (2026-11-04).
DEFAULT_CPU_MARGIN_SHARE: Final = 0.25

#: The fraction of the box's memory kept free above what its own user is using now.
DEFAULT_MEM_MARGIN: Final = 0.10

#: The spelling of one resource name: a ``[capabilities.<name>]`` table, a need-set marker.
RESOURCE_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*$')

_WHERE: Final = 'the machine file'

#: The one spelling of a seat count with NO CAP (``seats = "unlimited"``): a tool whose license admits any number of
#: concurrent solves (user ruling 2026-11-27: "Maxwell has no seat limit on any machine"). Sized as ``math.inf``, so
#: no claim is ever refused for lack of its seat; every other string is refused by name.
UNLIMITED: Final = 'unlimited'

#: The tools whose seats are VENDOR seats -- a license or a measured concurrency, never a guess: an undeclared
#: one is WITHHELD (:func:`withheld`, user ruling 2026-11-18), never lent 1. Every
#: :data:`~lab_commons.compute.toolchecks.CHECKS` row is here or in :data:`NON_VENDOR_CHECKS`.
VENDOR_TOOLS: Final = frozenset(
    {'aedt', 'femm', 'fluent', 'jmag', 'lsdyna', 'mapdl', 'matlab', 'maxwell', 'ngspice', 'plecs', 'simba'}
)

#: Checked tools that are not vendor seats: a runtime on a path, not a counted license.
NON_VENDOR_CHECKS: Final = frozenset({'node'})


class UndeclaredSeat(LookupError):
    """A vendor tool's seats were asked for and this machine declares no count: withheld, never lent 1.

    Its text is the one reason :func:`withheld` gives; nothing in the seat path raises it.
    """

    def __init__(self, tool: str) -> None:
        """Name *tool*, the machine file it is missing from, and the door that declares it."""
        from lab_commons.config import config_path  # noqa: PLC0415 -- the box layer reads no config at import

        self.tool = tool
        super().__init__(
            f'{tool}: a vendor tool with no declared seat count -- {config_path()} has no [capabilities.{tool}] '
            f'seats (a silent 1 hid 3 JMAG seats, ruling 2026-11-18). Declare [dev.vendors.{tool}] seats = <N> '
            f"in this machine's host file in hosts-config (hosts/<NAME>.toml), then "
            f'`python -m dev_lab.dev.devhost render --host <NAME> --from <clone> --write`'
        )


def seat_count(name: str, value: object) -> float:
    """Tool *name*'s declared ``seats`` *value* as a size: a positive int, or ``math.inf`` for :data:`UNLIMITED`.

    THE ONE PARSE of a seat count: :func:`provided` validates through it and every seat reader sizes through it.

    Raises:
        ValueError: *value* is neither a positive int nor :data:`UNLIMITED`; the message names the row and both forms.

    """
    if value == UNLIMITED:
        return math.inf
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    msg = f'{_WHERE}: [capabilities.{name}] seats = {value!r}: declare a positive int or "{UNLIMITED}" (no cap)'
    raise ValueError(msg)


def withheld(name: str, *, declared: bool) -> str | None:
    """THE ONE RULE for an undeclared vendor seat: why tool *name* lends nothing, or ``None`` when it may lend.

    A :data:`VENDOR_TOOLS` tool whose count is not *declared* is WITHHELD -- zero supply, the reason the
    :class:`UndeclaredSeat` text naming the machine file and the door -- and it is THAT tool alone: every other
    tool still sizes and supplies, so one undeclared row never aborts sizing, supply or admission. Every seat
    reader (:func:`provided`, :func:`seat_size`, :func:`lab_commons.box.providers.seat_sizes`, the ledger's
    refusal) asks this, and nothing else decides it.
    """
    return str(UndeclaredSeat(name)) if name in VENDOR_TOOLS and not declared else None


def seat_size(seats: Mapping[str, float], name: str) -> float:
    """The size of seat *name* in *seats*: its declared count; a vendor tool undeclared is 0 (:func:`withheld`).

    A name that is no vendor tool -- an exclusion seat such as an environment or an in-flight run -- is one seat.
    """
    if name in seats:
        return seats[name]
    return 0 if withheld(name, declared=False) else 1


@dataclass(frozen=True)
class ReserveTable:
    """The machine file's ``[reserve]``: OS HEADROOM kept free above what the OS reads in use now.

    It is not a reservation for people (ruling 2026-10-25): a person's or a session's use is DECLARED as an
    ordinary claim on the ledger (``python -m lab_commons.box claim``), and use nobody declared that the OS
    measures already counts. The margin only keeps the OS itself responsive: 1 core, but never more than
    ``cpu_margin_share`` of the cores, and 10% of memory.
    """

    cpu_margin: float = DEFAULT_CPU_MARGIN
    mem_margin: float = DEFAULT_MEM_MARGIN
    cpu_margin_share: float = DEFAULT_CPU_MARGIN_SHARE


def reserve_table(table: Mapping[str, Any]) -> ReserveTable:
    """Read ``[reserve]``; an unknown key or a margin out of range is refused."""
    unknown = sorted(set(table) - set(ReserveTable.__dataclass_fields__))
    cpu = table.get('cpu_margin', DEFAULT_CPU_MARGIN)
    share = table.get('cpu_margin_share', DEFAULT_CPU_MARGIN_SHARE)
    mem = table.get('mem_margin', DEFAULT_MEM_MARGIN)
    bad = [
        *(f'unknown key {key!r}' for key in unknown),
        *(['cpu_margin must be >= 0 cores'] if not isinstance(cpu, int | float) or cpu < 0 else []),
        *(
            ['cpu_margin_share must be a fraction in [0, 1]']
            if not isinstance(share, int | float) or not 0 <= share <= 1
            else []
        ),
        *(['mem_margin must be a fraction in [0, 1]'] if not isinstance(mem, int | float) or not 0 <= mem <= 1 else []),
    ]
    if bad:
        msg = f'{_WHERE} [reserve]: {"; ".join(bad)}'
        raise ValueError(msg)
    return ReserveTable(float(cpu), float(mem), cpu_margin_share=float(share))


def own_reserve(total: Mapping[str, float], table: ReserveTable) -> dict[str, float]:
    """The margin this box keeps above what its own user uses: ``{'cpu', 'mem_gb'}``, capped at the box.

    The cpu margin is ``cpu_margin`` cores but at most ``cpu_margin_share`` of them, so it scales down with the host.

    What the user USES is not here: it is in the OS reading the box supply is measured from
    (:mod:`lab_commons.box.supply`), so it is counted once, as it happens, and never declared.
    """
    cores = total.get('cpu', 0.0)
    wanted = {
        'cpu': min(table.cpu_margin, table.cpu_margin_share * cores),
        'mem_gb': table.mem_margin * total.get('mem_gb', 0.0),
    }
    return {name: min(amount, total.get(name, 0.0)) for name, amount in wanted.items()}


def provided(table: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The machine file's ``[capabilities]``: ``{name: facts}``; a malformed or RETIRED row is refused by name.

    A retired name (``capabilities.<name>`` in :mod:`lab_commons.hpc._retired_compute`) is never read as a resource:
    no alias, the refusal names what replaced it.

    ``seats`` is the DECLARED count (:func:`seat_count`: a positive int, or :data:`UNLIMITED`) -- a :data:`VENDOR_TOOLS`
    row without one is kept but :func:`withheld`, 1 for a marker such as ``workstation``;
    ``supply``, when given, is the argv of the probe that MEASURES it
    (:func:`lab_commons.box.providers.seat_sizes`); ``in_use = { process = [images] }``, when given, names the process
    images whose running count is the seats in use NOW (:func:`watched`, :func:`lab_commons.box.supply.observe`).
    """
    out: dict[str, dict[str, Any]] = {}
    for name, facts in table.items():
        if (row := COMPUTE.get(f'capabilities.{name}')) is not None:
            msg = f'{_WHERE}: [capabilities.{name}] is retired: {row[0]} -- delete the table'
            raise ValueError(msg)
        if not RESOURCE_NAME.match(name) or not isinstance(facts, dict):
            msg = f'{_WHERE}: [capabilities.{name}] must be a table named {RESOURCE_NAME.pattern}'
            raise ValueError(msg)
        seat_count(name, facts.get('seats', 1))
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
