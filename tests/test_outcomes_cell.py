"""A0.2: Reason, Cost, and the Cell invariants."""

from __future__ import annotations

import math

import pytest

from lab_commons.outcomes import Cell, Cost, Evidence, Reason, Status

OOM = Reason('out_of_memory', 'killed at the 8 GB claim')


def test_planted_control_an_out_of_memory_cell_with_a_value_is_refused() -> None:
    """PLANTED CONTROL (checklist A0.2): a killed run has no value to report."""
    with pytest.raises(ValueError, match='values'):
        Cell(Status.OUT_OF_MEMORY, values={'torque': 1.0}, reason=OOM)
    assert Cell(Status.OUT_OF_MEMORY, reason=OOM).values == {}


def test_only_ok_and_partial_carry_values() -> None:
    assert Cell(Status.OK, values={'torque': 1.5}).values == {'torque': 1.5}
    assert Cell(Status.PARTIAL, values={'torque': 1.5}, reason=Reason('truncated')).values['torque'] == 1.5
    carriers = {Status.OK, Status.PARTIAL}
    for status in set(Status) - carriers:
        with pytest.raises(ValueError, match='values'):
            Cell(status, values={'x': 1.0}, reason=Reason('any'))


def test_a_reason_is_owed_by_every_status_but_ok_and_pending() -> None:
    assert Cell(Status.OK).reason is None
    assert Cell(Status.PENDING).reason is None
    for status in set(Status) - {Status.OK, Status.PENDING}:
        with pytest.raises(ValueError, match='reason'):
            Cell(status)
        assert Cell(status, reason=Reason('why')).reason == Reason('why')


def test_nan_is_never_a_status() -> None:
    with pytest.raises(ValueError, match='NaN'):
        Cell(Status.OK, values={'torque': math.nan})
    with pytest.raises(TypeError):
        Cell(Status.OK, values={'flag': True})
    with pytest.raises(TypeError):
        Cell('ok', values={})  # type: ignore[arg-type]


def test_reason_kind_is_a_machine_token() -> None:
    assert Reason('infeasible', 'g1 > 0').kind == 'infeasible'
    for bad in ('', 'Has Space', 'UPPER', '1st'):
        with pytest.raises(ValueError, match='token'):
            Reason(bad)


def test_cost_fields_are_optional_and_non_negative() -> None:
    assert Cost() == Cost(None, None, None)
    assert Cost(wall_s=1.5).wall_s == 1.5
    with pytest.raises(ValueError, match='negative'):
        Cost(cpu_s=-1.0)


def test_a_cell_round_trips_through_its_plain_form() -> None:
    cell = Cell(
        Status.PARTIAL,
        values={'torque': 0.1 + 0.2, 'n': 3},
        reason=Reason('truncated', 'stopped at step 40'),
        evidence=Evidence.VALIDATED,
        cost=Cost(wall_s=2.0, peak_memory_bytes=1 << 30),
    )
    assert Cell.from_plain(cell.to_plain()) == cell
    assert Cell(Status.OK).evidence is Evidence.OBSERVED
    assert Cell.from_plain(Cell(Status.PENDING).to_plain()) == Cell(Status.PENDING)


def test_a_cell_is_immutable() -> None:
    cell = Cell(Status.OK, values={'a': 1.0})
    with pytest.raises(TypeError):
        cell.values['a'] = 2.0  # type: ignore[index]


def test_equal_cells_hash_equal() -> None:
    a = Cell(Status.OK, values={'a': 1.0}, cost=Cost(wall_s=1.0))
    b = Cell(Status.OK, values={'a': 1.0}, cost=Cost(wall_s=1.0))
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b, Cell(Status.OK, values={'a': 2.0})}) == 2
