"""``lab_commons.em`` — tier-2 family-shared EM quantity vocabulary (optional import).

Ported from motronics-studio's ``tests/unit/core/test_units.py`` (the EM-vocabulary
assertions), restricted to a representative subset of types/constants -- exhaustively
re-testing every one of the ~40 constants would just re-test pint. Also pins the tier
boundary: importing ``lab_commons.units`` must not import this module.
"""

import sys

import numpy as np
import pytest

from lab_commons.em import (
    CONSTANTS,
    AngleSpeedType,
    AngleType,
    Q_0mm,
    Q_0Nm,
    Q_0rpm,
    Q_360deg,
    TorqueType,
    is_equal_2DPoint,
)
from lab_commons.units import Q_


def test_torque_type_and_constant_are_newton_meter_dimensioned():
    assert TorqueType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'N*m'}
    q = Q_0Nm
    assert q.to('N*m').magnitude == 0.0
    assert q.dimensionality == Q_(1.0, 'N*m').dimensionality


def test_angle_type_and_q_360deg():
    assert AngleType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'deg'}
    assert Q_360deg.to('rad').magnitude == pytest.approx(2 * np.pi)


def test_angle_speed_type_and_q_0rpm():
    assert AngleSpeedType.__supertype__.__metadata__[1].json_schema_extra == {'unit': 'rad/s'}
    assert Q_0rpm.to('rad/s').magnitude == 0.0


def test_is_equal_2d_point():
    Q_0m = Q_(0, 'm')
    Q_1m = Q_(1, 'm')
    Q_1000mm = Q_(1000, 'mm')
    p1 = [Q_0m, Q_1m]
    p2 = [Q_0mm, Q_1000mm]
    assert is_equal_2DPoint(p1, p2)

    Q_180deg = Q_(180, 'deg')
    Q_pi_rad = Q_(np.pi, 'rad')
    p3 = [Q_0m, Q_180deg]
    p4 = [Q_0mm, Q_pi_rad]
    assert is_equal_2DPoint(p3, p4)


def test_constants_vacuum_permeability():
    assert CONSTANTS.vacuum_permeability.to('H/m').magnitude == pytest.approx(4 * np.pi * 1e-7)
    assert CONSTANTS.miu_0 is CONSTANTS.vacuum_permeability


def test_units_import_does_not_pull_in_em():
    """Importing lab_commons.units alone must not drag in the tier-2 em module."""
    sys.modules.pop('lab_commons.em', None)
    sys.modules.pop('lab_commons.units', None)
    import lab_commons.units  # noqa: F401

    assert 'lab_commons.em' not in sys.modules
