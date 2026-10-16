"""The machine file's resource facts, read in their one home: ``[reserve]`` margins and ``[capabilities]`` seats."""

from __future__ import annotations

import pytest

from lab_commons.box.machine import DEFAULT_CPU_MARGIN, DEFAULT_MEM_MARGIN, own_reserve, provided, reserve_table


def test_the_own_reserve_is_the_declared_margin_capped_at_the_box() -> None:
    """What the user USES is measured (lab_commons.box.supply); the reserve is only the margin above it."""
    table = reserve_table({})
    assert table.mem_margin == DEFAULT_MEM_MARGIN == 0.25
    total = {'cpu': 16.0, 'mem_gb': 64.0}
    assert own_reserve(total, table) == {'cpu': DEFAULT_CPU_MARGIN, 'mem_gb': 16.0}
    assert own_reserve({'cpu': 0.5, 'mem_gb': 64.0}, table)['cpu'] == 0.5, 'never more than the box'


def test_the_reserve_table_declares_margins_and_refuses_the_rest() -> None:
    table = reserve_table({'cpu_margin': 2, 'mem_margin': 0.5})
    assert (table.cpu_margin, table.mem_margin) == (2.0, 0.5)
    for bad in ({'delegates': ['bob']}, {'mem_margin': 1.5}, {'cpu_margin': -1}):
        with pytest.raises(ValueError, match=r'\[reserve\]'):
            reserve_table(bad)


def test_the_machine_file_declares_what_this_host_provides() -> None:
    assert provided({'jmag': {'seats': 4}, 'dev_checkout': {}}) == {'jmag': {'seats': 4}, 'dev_checkout': {}}
    assert provided({}) == {}


@pytest.mark.parametrize('table', [{'jmag': 4}, {'jmag': {'seats': 0}}, {'jmag': {'seats': 'four'}}, {'Linux': {}}])
def test_a_malformed_capability_is_refused_by_name(table: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r'\[capabilities'):
        provided(table)
