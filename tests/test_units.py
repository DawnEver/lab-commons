"""``lab_commons.units`` — tier-1 generic pint <-> pydantic (``Annotated``) machinery.

Ported from motronics-studio's ``tests/unit/core/test_units.py`` behavior, restricted to
the tier-1 subset (no EM vocabulary -- that's ``test_em.py``). Also pins the tier
boundary itself: importing this module must never import ``lab_commons.em``.
"""

import argparse
import sys
from argparse import ArgumentTypeError

import pytest
from pint import UndefinedUnitError as pint_UndefinedUnitError

from lab_commons.exceptions import QuantityException
from lab_commons.units import (
    Q_,
    BaseModel_with_q,
    PydanticQuantity,
    get_quantity_type,
    quantity_parser,
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
    # abs=0.0: the CLAIM is that a dump/reload round trip loses nothing, and 5.0 mm survives JSON
    # exactly. Any absolute floor here would let the round trip drop a digit and still read green.
    assert reloaded.length.to('mm').magnitude == pytest.approx(5.0, abs=0.0)


def test_units_import_does_not_pull_in_em():
    """Importing lab_commons.units alone must not drag in the tier-2 em module."""
    sys.modules.pop('lab_commons.em', None)
    sys.modules.pop('lab_commons.units', None)
    import lab_commons.units  # noqa: F401, PLC0415 -- the import IS the measurement, after the pop

    assert 'lab_commons.em' not in sys.modules


# ---------------------------------------------------------------------------------------------
# quantity_parser: the CLI half of "the unit lives in the VALUE".
#
# It exists because banning a unit from a NAME is only half a rule. A flag spelled
# `--slot-pitch-mm` with `type=float` teaches the reader the unit and hands pint nothing; the
# obvious repair -- drop `-mm` -- DELETES the unit instead of moving it. This is where it moves.
# ---------------------------------------------------------------------------------------------


def test_a_bare_number_takes_the_default_unit() -> None:
    """The compatibility direction: an existing `type=float` call site keeps accepting what it did."""
    parse = quantity_parser('mm')
    assert parse('12.5') == Q_(12.5, 'mm')
    assert parse(' 12.5 ') == Q_(12.5, 'mm')
    assert parse('1e-3') == Q_(0.001, 'mm'), 'exponent notation has an `e` in it and is not a unit'


def test_a_written_unit_wins_over_the_default() -> None:
    """The direction that makes the migration worth doing: the value carries its own unit."""
    parse = quantity_parser('mm')
    assert parse('12.5mm') == Q_(12.5, 'mm')
    assert parse('0.0125 m') == Q_(12.5, 'mm'), 'and it converts, which a bare float never could'
    assert parse('25um') == Q_(0.025, 'mm')


def test_an_angle_is_not_silently_rewritten_as_a_length() -> None:
    """THE CASE THAT FORCES A SYNTACTIC CHECK. `12.5` and `12.5 rad` are BOTH dimensionless to pint,
    so asking `is_dimensionless()` would read `12.5 rad` as a bare number and return 12.5 mm."""
    parse = quantity_parser('mm')
    assert parse('12.5 rad') == Q_(12.5, 'rad')
    assert parse('12.5 deg') == Q_(12.5, 'deg')
    assert not parse('12.5 rad').check('[length]'), 'an angle must not come back as a length'


def test_a_refusal_says_what_was_wrong_with_the_input() -> None:
    """ArgumentTypeError rather than ValueError: argparse DISCARDS a bare ValueError's message."""
    parse = quantity_parser('mm')
    for bad in ('hello', '12.5 furlongs_per_fortnight_ish', ''):
        with pytest.raises(ArgumentTypeError) as raised:
            parse(bad)
        assert str(raised.value), f'{bad!r} was refused without saying why'


def test_a_misspelled_default_unit_is_refused_at_construction_not_on_a_users_run() -> None:
    """A parser built with a typo must fail where it is WRITTEN, not the first time a user types."""
    with pytest.raises(pint_UndefinedUnitError):
        quantity_parser('metres_typo')
    with pytest.raises(ValueError, match='no default unit'):
        quantity_parser('   ')


def test_the_parser_is_usable_as_an_argparse_type() -> None:
    """The contract is argparse's `type=` protocol, so drive it through a real ArgumentParser."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--slot-pitch', type=quantity_parser('mm'), default=None)
    args = parser.parse_args(['--slot-pitch', '12.5'])
    assert args.slot_pitch == Q_(12.5, 'mm')
    args = parser.parse_args(['--slot-pitch', '1.25cm'])
    assert args.slot_pitch == Q_(12.5, 'mm')
    with pytest.raises(SystemExit):
        parser.parse_args(['--slot-pitch', 'banana'])
