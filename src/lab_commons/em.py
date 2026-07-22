"""Tier 2 — family-shared EM/physical quantity vocabulary (optional import).

Not project-specific, but shared across the motronics/optimi-lab/wdg-lab family of
motor/EM labs: every quantity ``NewType`` (``LengthType``, ``TorqueType``, ...) and every
``Q_*`` constant (``Q_0mm``, ``Q_360deg``, ...) lives here, isolated from tier 1 so that
``import lab_commons.units`` never drags this vocabulary along. Import this module
explicitly (``from lab_commons.em import TorqueType``) when a consumer needs it.

Depends on ``lab_commons.units`` (tier 1) for ``Q_``/``get_quantity_type``/``ureg`` --
never the reverse.

Provenance: extracted from motronics-studio's ``core/units.py``.
"""

from typing import Annotated, NewType

import numpy as np
from pint import Quantity as PintQuantityType
from pint import Unit
from pydantic import Field

from lab_commons.units import Q_, get_quantity_type, ureg

__all__ = [
    'CONSTANTS',
    'Q_0A',
    'Q_0H',
    'Q_0J',
    'Q_0T',
    'Q_0W',
    'Q_1A',
    'Q_1J',
    'Q_1N',
    'Q_1T',
    'Q_1V',
    'Q_1W',
    'Q_5A',
    'Q_10A',
    'Q_20A',
    'Q_100A',
    'AngleSpeedType',
    'AngleType',
    'AreaType',
    'Cartesian2DPoint',
    'Cartesian2DPoint_Array',
    'CurrentDensityType',
    'CurrentType',
    'DensityType',
    'ElecConductivityType',
    'EnergyType',
    'FluxDensityType',
    'FluxLinkageType',
    'ForceType',
    'FrequencyType',
    'InductanceType',
    'LengthType',
    'MMFType',
    'MagFieldIntensityType',
    'MagVectorPotentialType',
    'MassType',
    'PerTemperatureType',
    'PermeabilityType',
    'Polar2DPoint',
    'Polar2DPoint_Array',
    'PowerPerMassType',
    'PowerType',
    'Q_0A_per_m',
    'Q_0A_per_mm2',
    'Q_0Hz',
    'Q_0MS_per_m',
    'Q_0Nm',
    'Q_0Wb',
    'Q_0Wb_per_m',
    'Q_0deg',
    'Q_0degC',
    'Q_0kg',
    'Q_0mm',
    'Q_0mm2',
    'Q_0mm3',
    'Q_0ohm',
    'Q_0ohm_m',
    'Q_0rpm',
    'Q_0s',
    'Q_1A_per_mm2',
    'Q_1At',
    'Q_1Nm',
    'Q_1Wb',
    'Q_1Wb_per_m',
    'Q_1kg',
    'Q_1m2',
    'Q_1mm',
    'Q_1mm2',
    'Q_1mm3',
    'Q_1ohm',
    'Q_1rad',
    'Q_2pi',
    'Q_10Wb',
    'Q_20degC',
    'Q_50Hz',
    'Q_90deg',
    'Q_180deg',
    'Q_360deg',
    'Q_list2array',
    'ResistanceType',
    'ResistivityType',
    'TemperatureType',
    'TimeType',
    'TorqueType',
    'VoltageType',
    'VolumeType',
    'array2list_2Dpoint',
    'is_equal_2DPoint',
]

# Geometry
Q_1 = Q_(1.0)
LengthType = NewType('LengthType', get_quantity_type('mm'))
Q_0mm = Q_(0.0, 'mm')
Q_1mm = Q_(1.0, 'mm')
AreaType = NewType('AreaType', get_quantity_type('mm^2'))
Q_0mm2 = Q_(0.0, 'mm^2')
Q_1mm2 = Q_(1.0, 'mm^2')
Q_1m2 = Q_(1.0, 'm^2')
VolumeType = NewType('VolumeType', get_quantity_type('mm^3'))
Q_0mm3 = Q_(0.0, 'mm^3')
Q_1mm3 = Q_(1.0, 'mm^3')
AngleType = NewType('AngleType', get_quantity_type('deg'))
Q_360deg = Q_(360.0, 'deg')
Q_180deg = Q_(180.0, 'deg')
Q_90deg = Q_(90.0, 'deg')
Q_0deg = Q_(0.0, 'deg')
Q_1rad = Q_(1.0, 'rad')
Q_2pi = Q_360deg.to('rad')
Q_pi = Q_180deg.to('rad')
AngleSpeedType = NewType('AngleSpeedType', get_quantity_type('rad/s'))
Q_0rpm = Q_(0.0, 'rpm')
# Physics
TimeType = NewType('TimeType', get_quantity_type('s'))
Q_0s = Q_(0.0, 's')
MassType = NewType('MassType', get_quantity_type('kg'))
Q_0kg = Q_(0.0, 'kg')
Q_1kg = Q_(1.0, 'kg')
TemperatureType = NewType('TemperatureType', get_quantity_type('K'))
Q_0degC = Q_(0.0, 'degC')
Q_20degC = Q_(20.0, 'degC')
PerTemperatureType = NewType('PerTemperatureType', get_quantity_type('1/K'))
DensityType = NewType('DensityType', get_quantity_type('g/cm^3'))
FrequencyType = NewType('FrequencyType', get_quantity_type('Hz'))
Q_0Hz = Q_(0.0, 'Hz')
Q_1Hz = Q_(1.0, 'Hz')
Q_50Hz = Q_(50.0, 'Hz')
## Mechanics
ForceType = NewType('ForceType', get_quantity_type('N'))
Q_1N = Q_(1.0, 'N')
TorqueType = NewType('TorqueType', get_quantity_type('N*m'))
Q_0Nm = Q_(0.0, 'N*m')
Q_1Nm = Q_(1.0, 'N*m')
## Power / loss power
PowerType = NewType('PowerType', get_quantity_type('W'))
Q_0W = Q_(0.0, 'W')
Q_1W = Q_(1.0, 'W')
PowerPerMassType = NewType('PowerPerMassType', get_quantity_type('W/kg'))

## Energy / loss
EnergyType = NewType('EnergyType', get_quantity_type('J'))
Q_0J = Q_(0.0, 'J')
Q_1J = Q_(1.0, 'J')
## Electromagnetics
VoltageType = NewType('VoltageType', get_quantity_type('V'))
Q_1V = Q_(1.0, 'V')

CurrentType = NewType('CurrentType', get_quantity_type('A'))
Q_0A = Q_(0.0, 'A')
Q_1A = Q_(1.0, 'A')
Q_5A = Q_(5.0, 'A')
Q_10A = Q_(10.0, 'A')
Q_20A = Q_(20.0, 'A')
Q_100A = Q_(100.0, 'A')

### Current density
CurrentDensityType = NewType('CurrentDensityType', get_quantity_type('A/mm^2'))
Q_0A_per_mm2 = Q_(0.0, 'A/mm^2')
Q_1A_per_mm2 = Q_(1.0, 'A/mm^2')
### Flux density B
FluxDensityType = NewType('FluxDensityType', get_quantity_type('T'))
Q_0T = Q_(0.0, 'T')
Q_1T = Q_(1.0, 'T')
### Field intensity H
MagFieldIntensityType = NewType('MagFieldIntensityType', get_quantity_type('A/m'))
Q_0A_per_m = Q_(0.0, 'A/m')
### MMF
MMFType = NewType('MMFType', get_quantity_type('A*turn'))
Q_1At = Q_(1.0, 'A*turn')
### Magnetic vector potential A
MagVectorPotentialType = NewType('MagVectorPotentialType', get_quantity_type('Wb/m'))
Q_0Wb_per_m = Q_(0.0, 'Wb/m')
Q_1Wb_per_m = Q_(1.0, 'Wb/m')
### Conductivity
ElecConductivityType = NewType('ElecConductivityType', get_quantity_type('MS/m'))
Q_0MS_per_m = Q_(0.0, 'MS/m')
### Resistivity
ResistivityType = NewType('ResistivityType', get_quantity_type('ohm*m'))
Q_0ohm_m = Q_(0.0, 'ohm*m')
### Permeability
PermeabilityType = NewType('PermeabilityType', get_quantity_type('H/m'))
### Flux linkage
FluxLinkageType = NewType('FluxLinkageType', get_quantity_type('Wb'))
Q_0Wb = Q_(0.0, 'Wb')
Q_1Wb = Q_(1.0, 'Wb')
Q_10Wb = Q_(10.0, 'Wb')
### Inductance
InductanceType = NewType('InductanceType', get_quantity_type('H'))
Q_0H = Q_(0.0, 'H')
### Resistance
ResistanceType = NewType('ResistanceType', get_quantity_type('ohm'))
Q_0ohm = Q_(0.0, 'ohm')
Q_1ohm = Q_(1.0, 'ohm')

# Coordinate units can differ, so this doesn't use np.array.
# Fixed-length 2-element points: `list[X, Y]` was the "declaration that lies" pattern
# (list is homogeneous, single-type-argument only) -- `tuple[X, Y]` is the type that
# actually says "exactly two, positionally typed". Pydantic still accepts a JSON/TOML
# list on the wire (coerced to a tuple); json_schema_extra keeps the wire schema a list.
Cartesian2DPoint = Annotated[
    tuple[LengthType, LengthType],
    Field(..., json_schema_extra={'type': 'list', 'items': {'minItems': 2, 'maxItems': 2}}),
]
Polar2DPoint = Annotated[
    tuple[LengthType, AngleType],
    Field(..., json_schema_extra={'type': 'list', 'items': {'minItems': 2, 'maxItems': 2}}),
]


# `np.ndarray[LengthType]` was parametrizing ndarray's SHAPE type variable with a unit
# NewType -- LengthType/AngleType describe the pint unit these arrays carry, not a numpy
# shape or dtype, so it doesn't fit ndarray's actual generic slots. Left bare (Any shape,
# Any dtype); the unit information already lives in the NewType names, not the ndarray
# generic.
Cartesian2DPoint_Array = Annotated[
    tuple[np.ndarray, np.ndarray],
    Field(..., json_schema_extra={'type': 'list', 'items': {'minItems': 2, 'maxItems': 2}}),
]
Polar2DPoint_Array = Annotated[
    tuple[np.ndarray, np.ndarray],
    Field(..., json_schema_extra={'type': 'list', 'items': {'minItems': 2, 'maxItems': 2}}),
]


def Q_list2array(
    Q_list: list[PintQuantityType], Q_unit: str | Unit, has_unit: bool = True
) -> PintQuantityType | np.ndarray:
    """Convert a list of ``Quantity`` into an array, coercing to float.

    Args:
        Q_list (list[PintQuantityType]): list of pint ``Quantity``.
        Q_unit (str | Unit): target unit, either a string or a ``Unit`` object.
        has_unit (bool): whether to keep the unit, default True. If False, returns a bare array.

    Returns:
        PintQuantityType: the converted array, in ``Q_unit``.

    """
    q_array = np.array([q.to(Q_unit).magnitude for q in Q_list], dtype=float)
    if has_unit:
        q_array *= ureg.Unit(Q_unit)

    return q_array


def array2list_2Dpoint(
    point_array: Cartesian2DPoint_Array | Polar2DPoint_Array,
) -> list[Cartesian2DPoint | Polar2DPoint]:
    """Convert a numpy-friendly coordinate array into a list of coordinate points."""
    return [(point_array[0][idx], point_array[1][idx]) for idx, _ in enumerate(point_array[0])]


def is_equal_2DPoint(v1: Cartesian2DPoint | Polar2DPoint, v2: Cartesian2DPoint | Polar2DPoint):
    """Check whether two 2D coordinate points are equal, within tolerance.
    (Cannot compare directly with ``v1 == v2`` due to floating-point error.)
    Args:
        v1: first value to check.
        v2: second value to check.
    Returns:
        bool: whether they are equal.
    """
    return np.allclose(
        [v1[0].to_base_units().magnitude, v1[1].to_base_units().magnitude],
        [v2[0].to_base_units().magnitude, v2[1].to_base_units().magnitude],
    )


class CONSTANTS:
    """Physical constants.
    Args:
        vacuum_permeability (PermeabilityType): vacuum permeability, in H/m.
        miu_0 (PermeabilityType): vacuum permeability, in H/m.
    """

    vacuum_permeability: PermeabilityType = Q_(4 * np.pi * 1e-7, 'H/m')
    miu_0 = vacuum_permeability
