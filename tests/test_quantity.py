"""A declared config value carries its unit and is parsed ONCE, here (user ruling 2026-11-25).

`memory_gb = 32` puts the unit in the KEY NAME and leaves the number bare, so nothing checks it;
`memory = "32 GiB"` puts it in the VALUE, and :func:`lab_commons.quantity.parse` reads it through pint
to the base unit of its dimension. Every refusal names the key and the dimension it expected.
"""

from __future__ import annotations

import pytest

from lab_commons import quantity
from lab_commons.quantity import QuantityRefused, Renamed, form, parse, refuse_renamed

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
    assert set(quantity.DIMENSIONS) == {'duration', 'size'}
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
