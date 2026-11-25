"""A declared config value carries its unit, and is parsed ONCE, here.

THE RULING (user, 2026-11-25). ``memory_gb = 32`` puts the unit in the KEY NAME and leaves the number bare, so
nothing checks it: a reader who writes ``memory_gb = 32768`` meaning megabytes is never told. The family's config
writes ``memory = "32 GiB"`` instead -- the VALUE carries its unit -- and every reader turns it into a number
through :func:`parse`, the one boundary where a unit is read. Machine memory is written in GiB.

WHAT :func:`parse` RETURNS: the magnitude in the BASE unit of its dimension (:data:`DIMENSIONS`) -- seconds for a
duration, bytes for a size -- so every reader computes in one unit and converts nowhere else.

WHAT IT REFUSES, each naming the key and the dimension it expected, with a value to copy: a bare number (the
defect itself: ``32`` or ``"32"``), a value of another dimension (``"32 min"`` for a size), and a unit pint does
not know. Pint's unit names are the vocabulary: ``s``, ``min``, ``h``, ``d``; ``B``, ``MiB``, ``GiB``, ``MB``, ``GB``.

THE OLD KEYS MIGRATE ONCE (`FORMATS-MIGRATE-ONCE`): a reader keeps only the new shape and refuses an old,
unit-suffixed key by name through :func:`refuse_renamed`, which writes the replacement line from the old value --
``memory_gb = 32`` -> ``memory = "32 GiB"`` -- so the refusal is its own migration instruction.

WHERE PINT COMES FROM. The registry is pint's application registry, the one :mod:`lab_commons.units` binds as
``ureg``, so a quantity parsed here and one built there agree. It is imported inside :func:`parse`, never at module
load: hooks and CLIs import readers whose keys are often undeclared, and pint costs seconds to import. A machine
without pint (lab-commons installed without its ``units`` extra) is refused by name with the install that fixes it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = ['DIMENSIONS', 'Dimension', 'QuantityRefused', 'Renamed', 'form', 'parse', 'refuse_renamed']


#: A quantity's text opens with its number: pint alone reads ``"h"`` as one hour, which no one declared.
_NUMBER: Final = re.compile(r'^\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')


class QuantityRefused(ValueError):
    """A declared value that is not the quantity its key needs; the message names the key and the fix."""


@dataclass(frozen=True)
class Dimension:
    """One dimension a config value may have: its ROOT unit, the base unit a reader gets, and a value to copy.

    Matched on the root unit, never on pint's dimensionality: pint defines ``bit = []``, so a byte is DIMENSIONLESS
    there and ``"32 GiB"``, ``"32"`` and ``"32 rad"`` share one dimensionality -- the check the defect needs is
    which unit the value reduces to (``bit``, ``second``), measured 2026-11-25 against pint 0.25.
    """

    root: str
    base: str
    example: str


#: Every dimension a config value is read in, by the name a refusal uses.
DIMENSIONS: Final[Mapping[str, Dimension]] = {
    'duration': Dimension('second', 's', '10 min'),
    'size': Dimension('bit', 'B', '32 GiB'),
}


@dataclass(frozen=True)
class Renamed:
    """A retired unit-suffixed key's replacement: the new key, and the unit the old number was in."""

    key: str
    unit: str


def form(number: float, unit: str) -> str:
    """The TOML value that writes *number* in *unit*: ``form(32, 'GiB') == '"32 GiB"'``."""
    return f'"{number:g} {unit}"'


def _registry():  # noqa: ANN202 -- pint's registry type is pint's, imported only here
    try:
        import pint  # noqa: PLC0415 -- paid by a parse, never by an import (module docstring)
    except ModuleNotFoundError as missing:
        msg = 'reading a declared quantity needs pint: install lab-commons[units]'
        raise QuantityRefused(msg) from missing
    return pint.get_application_registry()


def _shown(value: object) -> str:
    return f'"{value}"' if isinstance(value, str) else repr(value)


def parse(key: str, value: object, dimension: str, *, positive: bool = False) -> float:
    """*value* (declared as *key*) in the base unit of *dimension* -- seconds or bytes.

    Raises:
        QuantityRefused: *value* is not a string with a number and a unit of *dimension* (or, with *positive*,
            not above zero); the message names *key* and *dimension* and a value to copy.
        ValueError: *dimension* is not one of :data:`DIMENSIONS` (a caller's bug, never a config fault).

    """
    if dimension not in DIMENSIONS:
        msg = f'dimension {dimension!r} is not one of {sorted(DIMENSIONS)}'
        raise ValueError(msg)
    want = DIMENSIONS[dimension]
    shown = f'{key} = {_shown(value)}'
    if not isinstance(value, str):
        bare = isinstance(value, int | float) and not isinstance(value, bool)
        why = 'is a bare number' if bare else 'is not a quantity'
        msg = f'{shown} {why}: write the {dimension} with its unit, such as "{_example(value, want)}"'
        raise QuantityRefused(msg)
    if not _NUMBER.match(value):
        msg = f'{shown} carries no number: write the {dimension} as a number and a unit, such as "{want.example}"'
        raise QuantityRefused(msg)
    ureg = _registry()
    try:
        quantity = ureg.Quantity(value)
    except Exception as bad:  # pint raises from several unrelated hierarchies; each is the same refusal
        msg = f'{shown} is not a {dimension} pint can read: write one such as "{want.example}"'
        raise QuantityRefused(msg) from bad
    if not isinstance(quantity, ureg.Quantity) or quantity.unitless:
        msg = f'{shown} is a bare number: write the {dimension} with its unit, such as "{_example(value, want)}"'
        raise QuantityRefused(msg)
    root = quantity.to_root_units().units
    if root != ureg.Unit(want.root):
        other = next((name for name, d in DIMENSIONS.items() if root == ureg.Unit(d.root)), 'another dimension')
        msg = f'{shown} is a {other}, not a {dimension}: write one such as "{want.example}"'
        raise QuantityRefused(msg)
    number = float(quantity.m_as(want.base))
    if not math.isfinite(number) or (positive and number <= 0):
        msg = f'{shown} must be positive and finite: write one such as "{want.example}"'
        raise QuantityRefused(msg)
    return number


def _example(value: object, want: Dimension) -> str:
    """The copyable fix for a bare *value*: its own number with the example's unit, else the example."""
    try:
        number = float(str(value).strip()) if not isinstance(value, bool) else None
    except ValueError:
        number = None
    return want.example if number is None else f'{number:g} {want.example.split()[-1]}'


def refuse_renamed(where: str, table: Mapping[str, object], renamed: Mapping[str, Renamed]) -> None:
    """Refuse any retired key of *renamed* in *table* (the ``[where]`` table), writing its replacement line.

    Raises:
        QuantityRefused: *table* declares a retired key; the message is the migration, from its own value.

    """
    for old, new in renamed.items():
        if old in table:
            value = table[old]
            line = f'{new.key} = {form(value, new.unit)}' if _is_number(value) else f'{new.key} = "<n> {new.unit}"'
            msg = (
                f'{where} {old} = {_shown(value)} is retired (user ruling 2026-11-25: a value carries its unit, '
                f'parsed once by lab_commons.quantity): write {line}'
            )
            raise QuantityRefused(msg)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
