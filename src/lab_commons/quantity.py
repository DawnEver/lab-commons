"""A declared config value carries its unit, and is parsed ONCE, here.

THE RULING (user, 2026-11-25). ``memory_gb = 32`` puts the unit in the KEY NAME and leaves the number bare, so
nothing checks it: a reader who writes ``memory_gb = 32768`` meaning megabytes is never told. The family's config
writes ``memory = "32 GiB"`` instead -- the VALUE carries its unit -- and every reader turns it into a number
through :func:`parse`, the one boundary where a unit is read. Machine memory is written in GiB.

WHAT :func:`parse` RETURNS: the magnitude in the BASE unit of its dimension (:data:`DIMENSIONS`) -- seconds for a
duration, bytes for a size, a fraction for a percentage (``"90 %"`` -> 0.9) -- so every reader computes in one unit
and converts nowhere else.

WHAT IT REFUSES, each naming the key and the dimension it expected, with a value to copy: a bare number (the
defect itself: ``32`` or ``"32"``), a value of another dimension (``"32 min"`` for a size), and a unit pint does
not know. Pint's unit names are the vocabulary: ``s``, ``min``, ``h``, ``d``; ``B``, ``MiB``, ``GiB``, ``MB``, ``GB``.

THE OLD KEYS MIGRATE ONCE (`FORMATS-MIGRATE-ONCE`): a reader keeps only the new shape and refuses an old,
unit-suffixed key by name through :func:`refuse_renamed`, which writes the replacement line from the old value --
``memory_gb = 32`` -> ``memory = "32 GiB"`` -- so the refusal is its own migration instruction.

THE FAMILY UNIT DOOR (v1 ruling D126 F1). Beyond config, this module is where EVERY unit crosses, in both
directions, so everything below it holds absolute, linear SI:

- :func:`parse_si` reads ``"<number> <unit>"`` of ANY dimension pint knows into a named SI unit (``"0.5 mm"`` ->
  ``5e-4`` for ``"m"``), refusing a bare number and a unit that does not convert, by key;
- :func:`to_si` / :func:`from_si` are the IR codec's boundary contract (``dev_lab.dev.irpython``: ``boundary =
  "lab_commons.quantity"``): affine units (``degC``) through pint; GAUGE when *reference* is a float -- the
  absolute SI reference the gauge value is relative to; DECIBELS (``dB``, ``dBA``) when *reference* is the pair
  ``(level, scale)`` -- ``level * 10**(dB / scale)``, scale 20 for a field quantity and 10 for a power;
- :func:`display` writes an SI value back in the unit a reader chose.

WHERE PINT COMES FROM. The registry is pint's application registry (:func:`registry`), one per process, so every
quantity in the family agrees; pint's own definitions are the vocabulary (``rpm`` included). Pint is imported inside
:func:`registry`, never at module load -- the heavy-optional exception: hooks and CLIs import readers whose keys are
often undeclared, and pint costs seconds to import (measured 1.6 s cold, 2026-11-26). A machine without pint
(lab-commons installed without its ``units`` extra) is refused by name with the install that fixes it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    'DECIBELS',
    'DIMENSIONS',
    'Dimension',
    'QuantityRefused',
    'Renamed',
    'display',
    'form',
    'from_si',
    'parse',
    'parse_si',
    'refuse_renamed',
    'registry',
    'to_si',
]


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
    'fraction': Dimension('percent', '', '90 %'),
}
#: A fraction is written in percent; pint reads ``"90 %"`` as the bare number 0.9 (unitless, like ``"0.9"``), so
#: the percent sign is checked in the TEXT, never by pint.
_PERCENT: Final = re.compile(r'^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:%|percent)\s*$')


@dataclass(frozen=True)
class Renamed:
    """A retired unit-suffixed key's replacement: the new key, and the unit the old number was in."""

    key: str
    unit: str


def form(number: float, unit: str) -> str:
    """The TOML value that writes *number* in *unit*: ``form(32, 'GiB') == '"32 GiB"'``."""
    return f'"{number:g} {unit}"'


#: The logarithmic units: converted against a ``(level, scale)`` reference here, never by pint.
DECIBELS: Final = frozenset({'dB', 'dBA'})


def registry():  # noqa: ANN201 -- pint's registry type is pint's, imported only here
    """The family's one unit registry: pint's application registry (module docstring)."""
    try:
        import pint  # noqa: PLC0415 -- paid by a parse, never by an import (module docstring)
    except ModuleNotFoundError as missing:
        msg = 'reading a declared quantity needs pint: install lab-commons[units]'
        raise QuantityRefused(msg) from missing
    return pint.get_application_registry()


def _shown(value: object) -> str:
    return f'"{value}"' if isinstance(value, str) else repr(value)


def parse(key: str, value: object, dimension: str, *, positive: bool = False) -> float:
    """*value* (declared as *key*) in the base unit of *dimension* -- seconds, bytes, or a fraction (90 % -> 0.9).

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
    if dimension == 'fraction':
        return _fraction(shown, value, want, positive=positive)
    ureg = registry()
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
        other = next((f'a {name}' for name, d in DIMENSIONS.items() if root == ureg.Unit(d.root)), 'another dimension')
        msg = f'{shown} is {other}, not a {dimension}: write one such as "{want.example}"'
        raise QuantityRefused(msg)
    number = float(quantity.m_as(want.base))
    if not math.isfinite(number) or (positive and number <= 0):
        msg = f'{shown} must be positive and finite: write one such as "{want.example}"'
        raise QuantityRefused(msg)
    return number


def _fraction(shown: str, text: str, want: Dimension, *, positive: bool) -> float:
    if (match := _PERCENT.match(text)) is None:
        msg = f'{shown} is not a percentage: write one such as "{want.example}"'
        raise QuantityRefused(msg)
    number = float(match.group(1)) / 100
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


def _converted(magnitude: object, unit: str, target: str) -> object:
    ureg = registry()
    try:
        return ureg.Quantity(magnitude, unit).to(target).magnitude
    except Exception as bad:  # pint raises from several unrelated hierarchies; each is the same refusal
        msg = f'"{unit}" does not convert to {target}'
        raise QuantityRefused(msg) from bad


def _decibel_pair(unit: str, si_unit: str, reference: object) -> tuple[float, float]:
    if not (isinstance(reference, tuple) and len(reference) == 2):  # noqa: PLR2004 -- the pair (level, scale)
        msg = f'{unit} of {si_unit} needs its (level, scale) reference: {si_unit} declares no dB reference'
        raise QuantityRefused(msg)
    level, scale = reference
    return float(level), float(scale)


def to_si(magnitude: object, unit: str, si_unit: str, *, reference: object = None) -> object:
    """*magnitude* in *unit* as an absolute *si_unit* magnitude (scalar, complex or array alike).

    *reference*: ``None``; a float, the absolute SI value a GAUGE magnitude is relative to; or ``(level, scale)``
    for a magnitude in :data:`DECIBELS`.

    Raises:
        QuantityRefused: *unit* does not convert to *si_unit*, or a dB magnitude has no ``(level, scale)``.

    """
    if unit in DECIBELS:
        import numpy as np  # noqa: PLC0415 -- the units extra's, paid only by a dB conversion

        level, scale = _decibel_pair(unit, si_unit, reference)
        return level * np.power(10.0, np.asarray(magnitude) / scale)
    absolute = _converted(magnitude, unit, si_unit)
    return absolute + reference if isinstance(reference, float) else absolute


def from_si(magnitude: object, si_unit: str, unit: str, *, reference: object = None) -> object:
    """An absolute *si_unit* *magnitude* in *unit*: gauge above a float *reference*, dB against ``(level, scale)``.

    Raises:
        QuantityRefused: *si_unit* does not convert to *unit*, or a dB unit has no ``(level, scale)``.

    """
    if unit in DECIBELS:
        import numpy as np  # noqa: PLC0415 -- the units extra's, paid only by a dB conversion

        level, scale = _decibel_pair(unit, si_unit, reference)
        return scale * np.log10(np.asarray(magnitude) / level)
    gauge = magnitude - reference if isinstance(reference, float) else magnitude  # type: ignore[operator]
    return _converted(gauge, si_unit, unit)


def parse_si(key: str, value: object, si_unit: str, *, reference: object = None) -> float:
    """*value* (declared as *key*), ``"<number> <unit>"`` of any dimension, as a float in *si_unit*.

    Raises:
        QuantityRefused: *value* is a bare number, carries no number, or its unit does not convert to *si_unit*;
            the message names *key* and a value to copy.

    """
    shown = f'{key} = {_shown(value)}'
    match = _NUMBER.match(value) if isinstance(value, str) else None
    if match is None or not value[match.end() :].strip():  # type: ignore[index]
        number = f'{value:g}' if _is_number(value) else '<n>'
        msg = f'{shown} is a bare number: write it with its unit, such as "{number} {si_unit}"'
        raise QuantityRefused(msg)
    unit = value[match.end() :].strip()  # type: ignore[index]
    try:
        return float(to_si(float(match.group(0)), unit, si_unit, reference=reference))  # type: ignore[arg-type]
    except QuantityRefused as refused:
        msg = f'{shown} does not convert to {si_unit}: {refused}'
        raise QuantityRefused(msg) from refused


def display(value_si: object, si_unit: str, unit: str, precision: int, *, reference: object = None) -> str:
    """*value_si* written in *unit* to *precision* significant digits: ``display(5e-4, 'm', 'mm', 3) == '0.5 mm'``."""
    shown = float(from_si(value_si, si_unit, unit, reference=reference))  # type: ignore[arg-type]
    return f'{shown:.{precision}g} {unit}'
