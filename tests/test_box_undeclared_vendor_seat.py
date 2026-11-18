"""A VENDOR tool with no declared seat count is REFUSED by name (user ruling 2026-11-18), never lent 1.

The silent 1 hid a workstation's 3 JMAG seats for hours. An exclusion seat (an environment, an in-flight run)
is not a vendor tool and keeps its one seat: it is the planted control that the refusal is scoped, not global.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, Capacity, hold
from lab_commons.box.machine import NON_VENDOR_CHECKS, VENDOR_TOOLS, UndeclaredSeat, provided, seat_size
from lab_commons.box.providers import ToolSeats, seat_sizes, size
from lab_commons.compute.model import Demand
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.config import config_path


@pytest.fixture
def machine_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'config.toml'
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(path))
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    path.write_text('[capabilities.femm]\nseats = 2\n', encoding='utf-8')
    return path


def _names_tool_path_and_door(error: UndeclaredSeat, tool: str) -> None:
    text = str(error)
    assert tool in text
    assert str(config_path()) in text
    assert f'[dev.vendors.{tool}] seats' in text
    assert 'hosts-config' in text
    assert 'devhost render' in text


def test_every_checked_tool_is_classified_vendor_or_not() -> None:
    assert set(CHECKS) <= VENDOR_TOOLS | NON_VENDOR_CHECKS
    assert not VENDOR_TOOLS & NON_VENDOR_CHECKS
    assert {'jmag', 'femm', 'ngspice', 'matlab', 'simba', 'plecs', 'maxwell'} <= VENDOR_TOOLS


@pytest.mark.usefixtures('machine_file')
def test_an_undeclared_vendor_seat_is_refused_naming_tool_path_and_door() -> None:
    with pytest.raises(UndeclaredSeat) as caught:
        seat_size({}, 'jmag')
    _names_tool_path_and_door(caught.value, 'jmag')
    assert seat_size({'jmag': 3}, 'jmag') == 3, 'CONTROL: a declared count is that count'
    assert seat_size({}, 'environment_ab12') == 1, 'CONTROL: an exclusion seat is not a vendor seat'


@pytest.mark.usefixtures('machine_file')
def test_the_box_size_refuses_an_undeclared_vendor_and_sizes_a_declared_one() -> None:
    capacity = Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2})
    assert size(capacity, ['femm']).resources['femm'] == 2
    with pytest.raises(UndeclaredSeat, match='plecs'):
        size(capacity, ['plecs'])


@pytest.mark.usefixtures('machine_file')
def test_a_hold_on_an_undeclared_vendor_seat_is_refused_before_it_is_lent() -> None:
    capacity = Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2})
    jmag = Demand({'jmag': 1, 'cpu': 0, 'mem_gb': 0})
    with pytest.raises(UndeclaredSeat) as caught, hold(jmag, 60, 't', 1, key='t', capacity=capacity):
        pass
    _names_tool_path_and_door(caught.value, 'jmag')
    with hold(Demand({'femm': 1, 'cpu': 0, 'mem_gb': 0}), 60, 't', 1, key='t', capacity=capacity):
        pass


def test_a_vendor_row_without_seats_is_refused_and_a_marker_row_is_not(machine_file: Path) -> None:
    machine_file.write_text('[capabilities.jmag]\n', encoding='utf-8')
    with pytest.raises(UndeclaredSeat) as caught:
        seat_sizes()
    _names_tool_path_and_door(caught.value, 'jmag')
    assert provided({'workstation': {}}) == {'workstation': {}}, 'CONTROL: a non-vendor marker needs no count'
    machine_file.write_text('[capabilities.jmag]\nseats = 3\n[capabilities.workstation]\n', encoding='utf-8')
    assert seat_sizes() == ({'jmag': 3, 'workstation': 1}, {})


@pytest.mark.usefixtures('machine_file')
def test_the_tool_seat_provider_supplies_no_undeclared_vendor() -> None:
    seats = ToolSeats(Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2}))
    assert 'femm' in seats.names
    assert not (seats.names & (VENDOR_TOOLS - {'femm'})), 'an undeclared vendor is not supplied a lent 1'
