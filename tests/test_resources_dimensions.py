"""``lab_commons.resources`` -- the resource DIMENSIONS as data, and the guard over their table.

The per-box broker that once lived beside this table is folded into the one box ledger (D2 2026-10-16);
its behaviour -- seats, a dead holder, a job outliving its client, a bounded wait -- is pinned by
``test_box.py`` and ``test_box_supply.py`` against :mod:`lab_commons.box`.
"""

import pytest

from lab_commons.resources.dimensions import (
    CPU,
    DIMENSIONS,
    DISK,
    GPU,
    MEMORY,
    SEATS,
    WALLCLOCK,
    Accounting,
    Dimension,
    Enforcement,
    _check_dimension_table,
)


def test_the_dimensions_with_values_are_declared_with_their_units() -> None:
    assert {SEATS.name, MEMORY.name, CPU.name, WALLCLOCK.name, DISK.name, GPU.name} <= set(DIMENSIONS)
    assert (MEMORY.unit, SEATS.unit, CPU.unit, WALLCLOCK.unit) == ('bytes', 'count', 'cores', 'seconds')


def test_the_table_guard_refuses_an_enforced_dimension_with_no_conservative_value() -> None:
    planted = Dimension('seats', 'count', Accounting.COUNTED, Enforcement.RECORDS, conservative=None)
    with pytest.raises(ValueError, match='conservative'):
        _check_dimension_table([planted])


def test_the_table_guard_refuses_a_value_on_a_dimension_nothing_enforces() -> None:
    planted = Dimension('disk', 'bytes', Accounting.BOX_GLOBAL, Enforcement.NONE, conservative=4)
    with pytest.raises(ValueError, match='enforced by nothing'):
        _check_dimension_table([planted])


def test_the_real_table_passes_its_own_guard() -> None:
    """PLANTED CONTROL for the two above: the shipped table is accepted."""
    _check_dimension_table(DIMENSIONS.values())
    enforced = [dimension for dimension in DIMENSIONS.values() if dimension.enforcement is not Enforcement.NONE]
    assert enforced, 'no dimension is enforced at all'
