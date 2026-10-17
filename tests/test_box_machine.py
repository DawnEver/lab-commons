"""The machine file's resource facts, read in their one home: ``[reserve]`` margins and ``[capabilities]`` seats."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lab_commons.box import Capacity, seat_supply
from lab_commons.box.machine import DEFAULT_CPU_MARGIN, DEFAULT_MEM_MARGIN, own_reserve, provided, reserve_table


def test_the_own_reserve_is_the_declared_margin_capped_at_the_box() -> None:
    """What the user USES is measured (lab_commons.box.supply); the reserve is only the margin above it."""
    table = reserve_table({})
    assert table.mem_margin == DEFAULT_MEM_MARGIN == 0.10
    total = {'cpu': 16.0, 'mem_gb': 64.0}
    assert own_reserve(total, table) == {'cpu': DEFAULT_CPU_MARGIN, 'mem_gb': 6.4}
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


def _capabilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    config = tmp_path / 'config.toml'
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(config))
    config.write_text(body, encoding='utf-8')


def _argv(code: str) -> str:
    return json.dumps([sys.executable, '-c', code])


def test_a_seat_count_is_measured_by_its_supply_probe_over_the_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capabilities(
        tmp_path, monkeypatch, f'[capabilities.jmag]\nseats = 5\nsupply = {_argv("print(2)")}\n[capabilities.femm]\n'
    )
    assert seat_supply() == {'jmag': 2, 'femm': 1}, 'measured beats declared; no probe falls back to the declaration'
    assert Capacity.read().seats == {'jmag': 2, 'femm': 1}


def test_a_supply_probe_that_cannot_say_falls_back_to_the_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capabilities(
        tmp_path, monkeypatch, f'[capabilities.jmag]\nseats = 5\nsupply = {_argv("import sys; sys.exit(3)")}\n'
    )
    assert seat_supply() == {'jmag': 5}
    _capabilities(tmp_path, monkeypatch, f'[capabilities.jmag]\nseats = 5\nsupply = {_argv("print(chr(120))")}\n')
    assert seat_supply() == {'jmag': 5}, 'PLANTED CONTROL: output that is no count is no measurement'


def test_a_malformed_supply_is_refused_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capabilities(tmp_path, monkeypatch, '[capabilities.jmag]\nsupply = "lmstat"\n')
    with pytest.raises(ValueError, match=r'capabilities\.jmag.*supply'):
        seat_supply()
