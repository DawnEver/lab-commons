"""A declared config value carries its unit and is parsed ONCE, here (user ruling 2026-11-25).

`memory_gb = 32` puts the unit in the KEY NAME and leaves the number bare, so nothing checks it;
`memory = "32 GiB"` puts it in the VALUE, and :func:`lab_commons.quantity.parse` reads it through pint
to the base unit of its dimension. Every refusal names the key and the dimension it expected.
"""

from __future__ import annotations

import math

import pytest

from lab_commons import quantity
from lab_commons.quantity import (
    QuantityRefused,
    Renamed,
    display,
    form,
    from_si,
    parse,
    parse_si,
    refuse_renamed,
    registry,
    to_si,
)

GIB = 1024**3


def test_a_size_parses_to_bytes() -> None:
    assert parse('memory', '32 GiB', 'size') == pytest.approx(32 * GIB, abs=1)
    assert parse('journal_max', '500 MB', 'size') == pytest.approx(500e6, abs=1)


def test_a_duration_parses_to_seconds() -> None:
    assert parse('test', '10 min', 'duration') == pytest.approx(600.0, abs=1e-9)
    assert parse('max_idle', '12 h', 'duration') == pytest.approx(43200.0, abs=1e-9)
    assert parse('max_age', '3 d', 'duration') == pytest.approx(3 * 86400.0, abs=1e-9)


@pytest.mark.parametrize('bare', [32, 32.0, '32', ' 32 '])
def test_a_bare_number_is_refused_naming_the_key_and_the_dimension(bare: object) -> None:
    """THE PLANTED DEFECT the ruling exists for: a number whose unit is nowhere."""
    with pytest.raises(QuantityRefused, match=r'memory = .*bare number.*size.*"32 GiB"'):
        parse('memory', bare, 'size')


def test_a_wrong_dimension_is_refused_naming_both() -> None:
    with pytest.raises(QuantityRefused, match=r'memory = "32 min" is a duration, not a size'):
        parse('memory', '32 min', 'size')
    with pytest.raises(QuantityRefused, match=r'test = "10 GiB" is a size, not a duration'):
        parse('test', '10 GiB', 'duration')
    with pytest.raises(QuantityRefused, match=r'memory = "32 rad" is another dimension, not a size'):
        parse('memory', '32 rad', 'size')


def test_an_unknown_unit_is_refused_naming_the_key() -> None:
    with pytest.raises(QuantityRefused, match=r'test = "10 fortnights_x" is not a duration.*"10 min"'):
        parse('test', '10 fortnights_x', 'duration')


@pytest.mark.parametrize('bad', [True, None, [1, 'h'], 'h', ''])
def test_a_non_quantity_is_refused(bad: object) -> None:
    with pytest.raises(QuantityRefused, match='max_idle'):
        parse('max_idle', bad, 'duration')


def test_positive_refuses_zero_and_negative() -> None:
    assert parse('tick', '0 s', 'duration') == 0.0
    with pytest.raises(QuantityRefused, match=r'tick = "0 s" must be positive'):
        parse('tick', '0 s', 'duration', positive=True)
    with pytest.raises(QuantityRefused, match=r'tick = "-1 min" must be positive'):
        parse('tick', '-1 min', 'duration', positive=True)


def test_an_unknown_dimension_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="dimension 'mass'"):
        parse('weight', '3 kg', 'mass')


def test_every_dimension_declares_its_base_and_example() -> None:
    """The examples a refusal teaches must themselves parse, or the door names a wall."""
    assert set(quantity.DIMENSIONS) == {'duration', 'size', 'fraction'}
    for name, dimension in quantity.DIMENSIONS.items():
        assert parse('k', dimension.example, name) > 0


def test_form_writes_the_new_value() -> None:
    assert form(32, 'GiB') == '"32 GiB"'
    assert form(159.9, 'GiB') == '"159.9 GiB"'
    assert form(3.0, 'd') == '"3 d"'


def test_a_renamed_key_is_refused_with_its_migration() -> None:
    """FORMATS-MIGRATE-ONCE: the old key is never read, and the refusal writes the new line for the reader."""
    renamed = {'memory_gb': Renamed('memory', 'GiB'), 'max_idle_hours': Renamed('max_idle', 'h')}
    with pytest.raises(QuantityRefused) as caught:
        refuse_renamed('[dev.resources]', {'cpu': 'x', 'memory_gb': 32}, renamed)
    text = str(caught.value)
    assert '[dev.resources] memory_gb = 32 is retired' in text
    assert 'write memory = "32 GiB"' in text


def test_a_table_without_a_renamed_key_passes() -> None:
    """The planted control's other side: the new shape is untouched."""
    refuse_renamed('[dev.resources]', {'memory': '32 GiB'}, {'memory_gb': Renamed('memory', 'GiB')})


def test_the_module_imports_with_the_standard_library_alone() -> None:
    """Pint is paid by a PARSE, never by an import: hooks and CLIs import readers they never call."""
    assert 'pint' not in quantity.__dict__


def test_a_percentage_is_a_fraction_and_its_sign_is_required() -> None:
    """Pint reads "90 %" as the bare 0.9, so the sign is checked in the text: "0.9" alone is the defect."""
    assert parse('disk_ceiling', '90 %', 'fraction') == pytest.approx(0.9, abs=1e-12)
    assert parse('disk_ceiling', '12.5 percent', 'fraction') == pytest.approx(0.125, abs=1e-12)
    for bad in ('0.9', '90', '90 pct', '90 GiB'):
        with pytest.raises(QuantityRefused, match=r'disk_ceiling = .* is not a percentage.*"90 %"'):
            parse('disk_ceiling', bad, 'fraction')
    with pytest.raises(QuantityRefused, match='bare number'):
        parse('disk_ceiling', 90, 'fraction')


# --- the family unit door (v1 D126 F1): any dimension to SI, the IR codec contract, display -------------------------


def test_any_dimension_parses_to_its_si_unit() -> None:
    assert parse_si('stack', '0.5 mm', 'm') == pytest.approx(5e-4, abs=1e-15)
    assert parse_si('torque', '12 kN*m', 'N*m') == pytest.approx(12e3, abs=1e-9)
    assert parse_si('speed', '3000 rpm', 'rad/s') == pytest.approx(100 * math.pi, abs=1e-9)
    assert parse_si('ambient', '20 degC', 'K') == pytest.approx(293.15, abs=1e-9)


def test_parse_si_refuses_a_bare_number_and_a_wrong_dimension_by_key() -> None:
    """PLANTED CONTROLS: the two defects the door exists to refuse."""
    with pytest.raises(QuantityRefused, match=r'stack = 0.5 is a bare number: .*"0.5 m"'):
        parse_si('stack', 0.5, 'm')
    with pytest.raises(QuantityRefused, match=r'stack = "0.5 s" does not convert to m'):
        parse_si('stack', '0.5 s', 'm')
    with pytest.raises(QuantityRefused, match=r'stack = "0.5 furlongz" does not convert to m'):
        parse_si('stack', '0.5 furlongz', 'm')


def test_affine_units_round_trip_through_the_door() -> None:
    assert to_si(20.0, 'degC', 'K') == pytest.approx(293.15, abs=1e-9)
    assert from_si(293.15, 'K', 'degC') == pytest.approx(20.0, abs=1e-9)
    assert to_si(68.0, 'degF', 'K') == pytest.approx(293.15, abs=1e-9)


def test_gauge_adds_the_absolute_reference_in_si() -> None:
    """A gauge value is relative to an absolute SI reference; the codec passes it as a float."""
    assert to_si(1.0, 'bar', 'Pa', reference=101325.0) == pytest.approx(201325.0, abs=1e-6)
    assert from_si(201325.0, 'Pa', 'bar', reference=101325.0) == pytest.approx(1.0, abs=1e-12)
    # PLANTED CONTROL: without the reference the same value is absolute.
    assert to_si(1.0, 'bar', 'Pa') == pytest.approx(1e5, abs=1e-6)


def test_decibels_convert_against_the_reference_and_scale() -> None:
    """Decibels are the boundary's arithmetic: level * 10**(dB/scale); the codec passes (level, scale)."""
    assert to_si(94.0, 'dB', 'Pa', reference=(20e-6, 20)) == pytest.approx(1.0024, abs=1e-4)
    assert from_si(1.0, 'Pa', 'dB', reference=(20e-6, 20)) == pytest.approx(93.979, abs=1e-3)
    assert to_si(30.0, 'dBA', 'W', reference=(1e-12, 10)) == pytest.approx(1e-9, abs=1e-21)
    with pytest.raises(QuantityRefused, match=r'dB.*no dB reference'):
        to_si(94.0, 'dB', 'Pa')


def test_arrays_and_complex_values_convert_elementwise() -> None:
    import numpy as np  # noqa: PLC0415 -- the units extra's own dependency, used only here

    got = to_si(np.array([1.0, 2.0]), 'mm', 'm')
    assert np.allclose(got, [1e-3, 2e-3], rtol=0, atol=1e-15)
    assert to_si(1 + 2j, 'mA', 'A') == pytest.approx(1e-3 + 2e-3j, abs=1e-15)


def test_to_si_refuses_a_unit_of_another_dimension() -> None:
    with pytest.raises(QuantityRefused, match=r'"s" does not convert to m'):
        to_si(1.0, 's', 'm')


def test_display_writes_a_si_value_in_the_chosen_unit() -> None:
    assert display(5e-4, 'm', 'mm', 3) == '0.5 mm'
    assert display(293.15, 'K', 'degC', 4) == '20 degC'
    assert display(1.0, 'Pa', 'dB', 4, reference=(20e-6, 20)) == '93.98 dB'


def test_the_registry_is_the_one_application_registry() -> None:
    import pint  # noqa: PLC0415 -- the one comparison this test makes

    assert registry() is pint.get_application_registry()
    assert registry().Quantity(1, 'rpm').to('rad/s').magnitude == pytest.approx(2 * math.pi / 60, abs=1e-15)
