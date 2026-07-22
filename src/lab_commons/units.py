"""Tier 1 — generic pint <-> pydantic machinery (Annotated design).

No EM/domain vocabulary lives here: no quantity-type NewTypes, no ``Q_*`` constants.
Test: could a lab doing something unrelated (chemistry, finance) use this module
unchanged? Yes, for everything in it.

Provenance: extracted from motronics-studio's ``core/units.py`` (the maintainer-designated
canonical design, 2026-07-22: "the ``Annotated`` approach, not a ``PydanticQuantity``
core-schema registration") -- this module carries only the generic plumbing; the family's
EM quantity types/constants (``LengthType``, ``TorqueType``, ``Q_0Nm``, ...) are tier 2,
in ``lab_commons.em``, which imports FROM this module and never the reverse.
"""

import warnings
from typing import Annotated

import numpy as np
from pint import Quantity as PintQuantityType
from pint import UnitStrippedWarning as pint_UnitStrippedWarning
from pint import get_application_registry
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, GetCoreSchemaHandler
from pydantic_core import core_schema

from lab_commons.exceptions import QuantityException

# UnitStrippedWarning fires whenever a Quantity is downcast to ndarray -- routine
# at the SI boundary, so silence it here where pint integration lives.
warnings.filterwarnings('ignore', category=pint_UnitStrippedWarning)

__all__ = [
    'Q_',
    'BaseModel_with_q',
    'PydanticQuantity',
    'get_quantity_type',
    'pydantic_config_dict_with_q',
    'ureg',
]

# Application-level registry, so every process shares one pint unit registry.
ureg = get_application_registry()


Q_ = ureg.Quantity
"""Converts a string/dict/tuple into a ``Quantity``; integer magnitudes are cast to float."""


class PydanticQuantity:
    """Lets Pydantic validate/serialize a pint ``Quantity``."""

    @classmethod
    def __get_pydantic_core_schema__(cls, _, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls.validate,
            handler(Q_),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def validate(cls, v) -> PintQuantityType:
        """Args:
            v: value to validate.
        Returns:
            PintQuantityType: the validated value.
        Raises:
            QuantityException: if ``v`` is not a ``Quantity``.

        """
        if not isinstance(v, PintQuantityType):
            msg = 'Expected pint.Quantity'
            raise QuantityException(msg)
        return v


def get_quantity_type(
    default_unit: str,
) -> type[PintQuantityType]:
    """Build an ``Annotated`` type for a physical quantity carrying a default unit.

    NOTE (ported from motronics, MANUAL-20260717-386 ratchet): the declared return type is
    deliberately NOT what this returns at runtime (an ``Annotated[...]`` special form,
    not a ``type``) -- pyright flags the mismatch at the single `return` below. Fixing it
    honestly (``-> Any``) was tried and makes it WORSE: every ``NewType(..., get_quantity_type(...))``
    call site downstream fails with "second argument to NewType must be a known class,
    not Any", because ``NewType`` requires a real class and ``Annotated`` structurally
    isn't one -- pyright silently accepted the lying `type[PintQuantityType]` there only
    because it trusted the DECLARED return type, not the real one. Kept as the honest
    (if imprecise) declared type, with a suppression here rather than a cast that would
    hide the mismatch entirely -- greppable, and this docstring is the ceiling.
    """
    return Annotated[  # pyright: ignore[reportReturnType]
        PintQuantityType,
        BeforeValidator(Q_),
        Field(..., json_schema_extra={'unit': default_unit}),  # base unit
        PydanticQuantity,
    ]


pydantic_config_dict_with_q = ConfigDict(
    str_to_lower=True,
    strict=True,
    extra='forbid',
    arbitrary_types_allowed=True,
)


class BaseModel_with_q(BaseModel):
    """Pydantic ``BaseModel`` supporting pint quantity validation and ``np.ndarray`` serialization."""

    model_config = pydantic_config_dict_with_q

    def model_dump(self, **kwargs):
        """``mode='python'`` will serialize ``np.ndarray`` to a list but not a Quantity's unit.
        ``mode='json'`` serializes a Quantity's unit but not ``np.ndarray`` to a list.
        """
        data = super().model_dump(**kwargs)
        for k, v in data.items():
            if isinstance(v, np.ndarray):
                data[k] = v.tolist()
        return data
