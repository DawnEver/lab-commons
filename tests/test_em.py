"""``lab_commons.em`` — tier-2 family-shared EM quantity vocabulary (optional import).

Ported from motronics-studio's ``tests/unit/core/test_units.py`` (the EM-vocabulary
assertions), restricted to a representative subset of types/constants -- exhaustively
re-testing every one of the ~40 constants would just re-test pint. Also pins the tier
boundary: importing ``lab_commons.units`` must not import this module.
"""

import sys

import numpy as np
import pytest

from lab_commons import em
from lab_commons.em import (
    CONSTANTS,
    AngleSpeedType,
    AngleType,
    Q_0mm,
    Q_0Nm,
    Q_0rpm,
    Q_360deg,
    TorqueType,
    build_em_types,
    is_equal_2DPoint,
)
from lab_commons.exceptions import QuantityException
from lab_commons.units import Q_, BaseModel_with_q


def test_torque_type_and_constant_are_newton_meter_dimensioned() -> None:
    assert TorqueType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'N*m'}
    q = Q_0Nm
    assert q.to('N*m').magnitude == 0.0
    assert q.dimensionality == Q_(1.0, 'N*m').dimensionality


def test_angle_type_and_q_360deg() -> None:
    assert AngleType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'deg'}
    # FLOOR IN RADIANS, the unit of the left-hand side: 1e-12 rad is ~2e-7 arcsecond, orders below
    # any angle a motor geometry resolves, while the deg->rad conversion's own error is one ulp of
    # 6.28 (~1e-15). A floor stated in degrees here would have been 1000x looser than it reads.
    assert Q_360deg.to('rad').magnitude == pytest.approx(2 * np.pi, abs=1e-12)


def test_angle_speed_type_and_q_0rpm() -> None:
    assert AngleSpeedType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'rad/s'}
    assert Q_0rpm.to('rad/s').magnitude == 0.0


def test_is_equal_2d_point() -> None:
    # N806 waived five times here: `Q_<magnitude><unit>` is pint's own spelling for a quantity
    # literal, and it is the family's -- wdg-lab imports `Q_0deg` BY THAT NAME. Lower-casing these
    # would make the fixture read like an ordinary float, which is the confusion pint exists to end.
    Q_0m = Q_(0, 'm')  # noqa: N806
    Q_1m = Q_(1, 'm')  # noqa: N806
    Q_1000mm = Q_(1000, 'mm')  # noqa: N806
    p1 = [Q_0m, Q_1m]
    p2 = [Q_0mm, Q_1000mm]
    assert is_equal_2DPoint(p1, p2)

    Q_180deg = Q_(180, 'deg')  # noqa: N806
    Q_pi_rad = Q_(np.pi, 'rad')  # noqa: N806
    p3 = [Q_0m, Q_180deg]
    p4 = [Q_0mm, Q_pi_rad]
    assert is_equal_2DPoint(p3, p4)


def test_constants_vacuum_permeability() -> None:
    # The VALUE is 1.2566e-6 H/m, so a floor must be scaled to it rather than to 1: 1e-18 H/m is
    # 1e-12 of the constant. A habitual abs=1e-12 would have been 1e-6 RELATIVE here and would have
    # accepted a permeability wrong in its seventh digit -- the exact inversion this rule names.
    assert CONSTANTS.vacuum_permeability.to('H/m').magnitude == pytest.approx(4 * np.pi * 1e-7, abs=1e-18)
    assert CONSTANTS.miu_0 is CONSTANTS.vacuum_permeability


def test_module_level_names_are_the_default_build_captured_at_import() -> None:
    """The module-level names ARE ``build_em_types()``'s default build.

    (``TorqueType``, ...) come from ``em._default``, captured once at import time -- not merely
    equivalent to one, THE one.
    """
    assert em._default.TorqueType is TorqueType
    assert em._default.AngleType is AngleType
    assert em._default.LengthType is em.LengthType
    assert em._default.Cartesian2DPoint is em.Cartesian2DPoint


def _unit_of(quantity_newtype) -> dict:
    return quantity_newtype.__supertype__.__metadata__[-2].json_schema_extra


def test_build_em_types_is_independent_per_call() -> None:
    """Two independent ``build_em_types()`` calls produce two independent vocabularies.

    New ``NewType`` objects each time -- required for injection: a consumer's custom-exception
    build must never alias or mutate the shared default (or another consumer's) vocabulary.
    """
    first = build_em_types()
    second = build_em_types()
    assert first.TorqueType is not second.TorqueType
    assert first.TorqueType is not TorqueType
    # But every build agrees on the underlying unit -- only the exception-raising marker
    # (and therefore object identity) differs per call, not the physical meaning.
    assert _unit_of(first.TorqueType) == _unit_of(second.TorqueType) == _unit_of(TorqueType) == {'unit': 'N*m'}


def _pydantic_quantity_marker(quantity_newtype) -> type:
    """Extract the ``PydanticQuantity``-shaped marker class from a ``NewType``.

    Read off its ``Annotated`` supertype -- the same class-method-level access the existing
    ``test_pydantic_quantity_validate``/``test_pydantic_quantity_rejects_a_raw_non_quantity...``
    tests in ``test_units.py`` use, and for the same documented reason: ``BeforeValidator(Q_)``
    runs first in a real model field and ``Q_`` accepts (wraps, doesn't reject) almost any input
    by construction, so the rejection contract is only directly demonstrable at this level.
    """
    return quantity_newtype.__supertype__.__metadata__[-1]


def test_build_em_types_injects_a_custom_quantity_exception() -> None:
    """The whole point of the injection.

    A vocabulary built with a CUSTOM exception class raises THAT class (not the shared
    ``QuantityException``) on a non-``Quantity`` value -- which proves the raise site is routed to the injected class,
    not bridged via MRO/inheritance.
    """

    class CustomQuantityException(Exception):
        """A consumer's own exception hierarchy, unrelated to lab_commons.exceptions'."""

    custom = build_em_types(quantity_exception=CustomQuantityException)
    assert custom.TorqueType is not TorqueType  # a genuinely separate vocabulary

    with pytest.raises(CustomQuantityException, match=r'Expected pint\.Quantity'):
        _pydantic_quantity_marker(custom.TorqueType).validate(1)

    # The shared default vocabulary is UNCHANGED by the custom build -- still raises the
    # shared QuantityException, never CustomQuantityException.
    with pytest.raises(QuantityException, match=r'Expected pint\.Quantity'):
        _pydantic_quantity_marker(TorqueType).validate(1)

    # And a real model field built from the custom vocabulary still round-trips a valid value
    # (the injection only changes the REJECTION path, not normal validation).
    class Model(BaseModel_with_q):
        torque: custom.TorqueType

    m = Model(torque=Q_0Nm)
    assert m.torque.to('N*m').magnitude == 0.0


def test_units_import_does_not_pull_in_em() -> None:
    """Importing lab_commons.units alone must not drag in the tier-2 em module."""
    sys.modules.pop('lab_commons.em', None)
    sys.modules.pop('lab_commons.units', None)
    import lab_commons.units  # noqa: F401, PLC0415 -- the import IS the measurement, after the pop

    assert 'lab_commons.em' not in sys.modules
