"""``lab_commons.units`` — tier-1 generic pint <-> pydantic (``Annotated``) machinery.

Ported from motronics-studio's ``tests/unit/core/test_units.py`` behavior, restricted to
the tier-1 subset (no EM vocabulary -- that's ``test_em.py``). Also pins the tier
boundary itself: importing this module must never import ``lab_commons.em``.
"""

import sys

import pytest

from lab_commons.exceptions import QuantityException
from lab_commons.units import (
    Q_,
    BaseModel_with_q,
    PydanticQuantity,
    get_quantity_type,
    ureg,
)


def test_q_round_trip():
    q = Q_(1.0, 'mm')
    assert q.to('m').magnitude == 0.001


def test_ureg_is_the_registry_backing_q():
    assert Q_ is ureg.Quantity


def test_pydantic_quantity_validate():
    q = Q_(0.0, 'mm')
    assert PydanticQuantity.validate(q) == q
    with pytest.raises(QuantityException, match=r'Expected pint\.Quantity'):
        PydanticQuantity.validate(1)


def test_get_quantity_type_builds_an_annotated_field_model():
    class Model(BaseModel_with_q):
        length: get_quantity_type('mm')

    m = Model(length=Q_(5.0, 'mm'))
    assert m.length.to('m').magnitude == 0.005


def test_pydantic_quantity_rejects_a_raw_non_quantity_at_the_class_level():
    """The field itself is permissive: ``get_quantity_type``'s ``BeforeValidator(Q_)`` runs
    FIRST, and ``Q_`` (pint's parser) accepts most inputs (int/str/list) by construction --
    so ``PydanticQuantity.validate`` (the piece that actually raises ``QuantityException`` on
    a non-``Quantity``) never sees a rejectable value once a model field has run.  The
    rejection contract is real, but only demonstrable at the class-method level (matches
    motronics' own ``test_pydantic_quantity_validate``); a bad-unit-string INTO a full model
    field instead surfaces pint's own ``UndefinedUnitError`` uncaught by pydantic (pint's
    error is an ``AttributeError`` subclass, not one of the exception types pydantic wraps
    into ``ValidationError``) -- a known permissiveness of this design, not asserted here as
    something it is not.
    """
    with pytest.raises(QuantityException, match=r'Expected pint\.Quantity'):
        PydanticQuantity.validate('not-a-quantity')


def test_pydantic_model_dump_and_reload_round_trips():
    class Model(BaseModel_with_q):
        length: get_quantity_type('mm')

    m = Model(length=Q_(5.0, 'mm'))
    dumped = m.model_dump(mode='json')
    reloaded = Model.model_validate(dumped)
    assert reloaded.length.to('mm').magnitude == pytest.approx(5.0)


def test_units_import_does_not_pull_in_em():
    """Importing lab_commons.units alone must not drag in the tier-2 em module."""
    sys.modules.pop('lab_commons.em', None)
    sys.modules.pop('lab_commons.units', None)
    import lab_commons.units  # noqa: F401

    assert 'lab_commons.em' not in sys.modules
