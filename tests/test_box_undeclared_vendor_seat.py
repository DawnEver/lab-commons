"""A VENDOR tool with no declared seat count is WITHHELD (user ruling 2026-11-18): zero supply, never lent 1.

The silent 1 hid a workstation's 3 JMAG seats for hours, so an undeclared vendor lends nothing. But it is
THAT tool alone: one undeclared row never aborts sizing, supply or admission for any other tool. The rule is
:func:`~lab_commons.box.machine.withheld`; its reason is the :class:`UndeclaredSeat` text naming the file and
the door. An exclusion seat (an environment, an in-flight run) is not a vendor tool and keeps its one seat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, Capacity, Refused, hold
from lab_commons.box.machine import (
    NON_VENDOR_CHECKS,
    VENDOR_TOOLS,
    UndeclaredSeat,
    provided,
    seat_size,
    withheld,
)
from lab_commons.box.providers import ToolSeats, measured, seat_sizes, size
from lab_commons.box.supply import Reading
from lab_commons.compute.model import Demand
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.config import config_path
from lab_commons.resources.model import Kind, kind_of

_IDLE = Reading(cores=4.0, idle_cores=4.0, total_bytes=2**31, available_bytes=2**31)


@pytest.fixture
def machine_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'config.toml'
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(path))
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    path.write_text('[capabilities.femm]\nseats = 2\n', encoding='utf-8')
    return path


def _names_tool_path_and_door(text: str, tool: str) -> None:
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
def test_the_one_rule_withholds_an_undeclared_vendor_with_the_undeclared_seat_text() -> None:
    why = withheld('jmag', declared=False)
    assert why == str(UndeclaredSeat('jmag')), 'the same message as UndeclaredSeat, one source'
    _names_tool_path_and_door(why or '', 'jmag')
    assert withheld('jmag', declared=True) is None, 'CONTROL: a declared vendor is not withheld'
    assert withheld('environment_ab12', declared=False) is None, 'CONTROL: an exclusion seat is no vendor seat'


@pytest.mark.usefixtures('machine_file')
def test_an_undeclared_vendor_seat_sizes_zero_never_one() -> None:
    assert seat_size({}, 'jmag') == 0
    assert seat_size({'jmag': 3}, 'jmag') == 3, 'CONTROL: a declared count is that count'
    assert seat_size({}, 'environment_ab12') == 1, 'CONTROL: an exclusion seat is not a vendor seat'


@pytest.mark.usefixtures('machine_file')
def test_the_box_size_withholds_an_undeclared_vendor_and_sizes_a_declared_one() -> None:
    whole = size(Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2}), ['femm', 'plecs']).resources
    assert whole['femm'] == 2
    assert whole['plecs'] == 0


@pytest.mark.usefixtures('machine_file')
def test_a_hold_on_an_undeclared_vendor_is_refused_by_name_and_a_declared_one_runs() -> None:
    capacity = Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2})
    jmag = Demand({'jmag': 1, 'cpu': 0, 'mem_gb': 0})
    with pytest.raises(Refused) as caught, hold(jmag, 60, 't', 1, key='t', capacity=capacity):
        pass
    _names_tool_path_and_door(str(caught.value), 'jmag')
    with hold(Demand({'femm': 1, 'cpu': 0, 'mem_gb': 0}), 60, 't', 1, key='t', capacity=capacity):
        pass


def test_an_undeclared_femm_is_withheld_while_a_declared_jmag_supplies_three(machine_file: Path) -> None:
    """PLANTED CONTROL: an undeclared femm row next to a declared jmag at 3 seats -- jmag still supplies 3."""
    machine_file.write_text('[capabilities.jmag]\nseats = 3\n[capabilities.femm]\n', encoding='utf-8')
    sizes, blind = seat_sizes()
    assert sizes == {'jmag': 3}
    assert set(blind) == {'femm'}
    _names_tool_path_and_door(blind['femm'], 'femm')
    capacity = Capacity.read()
    assert capacity.seats['jmag'] == 3
    assert capacity.blind['femm'] == blind['femm']
    supply = measured(capacity, [], _IDLE, seats=['jmag', 'femm'])
    assert supply.resources['jmag'] == 3
    assert supply.resources['femm'] == 0
    assert set(provided({'jmag': {'seats': 3}, 'femm': {}})) == {'jmag', 'femm'}
    assert kind_of('femm') is Kind.OCCUPANCY
    assert Reading.now([], sample_s=0.0).cores > 0, 'the OS reading observes seats past an undeclared row'


def test_with_every_vendor_declared_nothing_is_withheld(machine_file: Path) -> None:
    """PLANTED CONTROL: every vendor declared -- sizes and blindness are exactly the declaration."""
    machine_file.write_text('[capabilities.jmag]\nseats = 3\n[capabilities.workstation]\n', encoding='utf-8')
    assert seat_sizes() == ({'jmag': 3, 'workstation': 1}, {})
    assert provided({'workstation': {}}) == {'workstation': {}}, 'CONTROL: a non-vendor marker needs no count'


@pytest.mark.usefixtures('machine_file')
def test_the_tool_seat_provider_supplies_no_undeclared_vendor() -> None:
    seats = ToolSeats(Capacity(cpu=4.0, mem_bytes=2**30, seats={'femm': 2}))
    assert 'femm' in seats.names
    assert not (seats.names & (VENDOR_TOOLS - {'femm'})), 'an undeclared vendor is not supplied a lent 1'
