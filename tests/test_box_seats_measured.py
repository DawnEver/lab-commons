"""A TOOL SEAT IS SUPPLY LIKE A CORE (2026-10-25): its free count is MEASURED, not only declared.

A person on a workstation starts a JMAG solve from Designer or the Scheduler, outside the ledger; the box must
see that seat taken. So a ``[capabilities.<name>] in_use = { process = [...] }`` row names the solver images the
OS process table is counted by, and the free count is

    declared seats - max(seats held by ledger claims, seats observed in use) - [reserve] seat_margin.<name>

MAX, not sum: a ledger claim's own solver is also a process the table shows. An observation that cannot be
read lends NOTHING for that seat and says why -- a vacuous green is the family's worst defect.
"""

from __future__ import annotations

import pytest

from lab_commons.box import GB, Capacity, Holding
from lab_commons.box.ledger import _decide
from lab_commons.box.machine import provided, reserve_table, watched
from lab_commons.box.supply import Reading, observe, room
from lab_commons.compute.model import Demand
from lab_commons.compute.presence import Presence
from lab_commons.compute.probe import box

_JMAG = {'jmag': ('jam.exe', 'jam_c.exe')}


def _cap(*, seats: int = 4, margin: int = 0, watch: bool = True) -> Capacity:
    return Capacity(
        cpu=7.0,
        mem_bytes=12 * GB,
        seats={'jmag': seats},
        kept_seats={'jmag': margin} if margin else {},
        watched=frozenset({'jmag'}) if watch else frozenset(),
    )


def _reading(in_use: int | None) -> Reading:
    seen, unseen = ({}, {'jmag': 'the process table could not be read'}) if in_use is None else ({'jmag': in_use}, {})
    return Reading(8.0, 8.0, 16 * GB, 16 * GB, seats_in_use=seen, unseen=unseen)


def _held(n: int) -> list[Holding]:
    return [Holding(f'forge {i}', 100 + i, 0, 0, {'jmag': 1}, 0.0) for i in range(n)]


def test_a_quiet_watched_box_lends_its_declared_seats() -> None:
    """PLANTED CONTROL: nobody solves, nothing held, no margin -- the measured count IS the declaration."""
    assert room(_cap(), [], _reading(0))['jmag'] == 4


def test_a_solve_outside_the_ledger_takes_its_seat() -> None:
    assert room(_cap(), [], _reading(3))['jmag'] == 1


def test_a_claims_own_solver_is_not_counted_twice() -> None:
    """Two ledger claims whose two solvers the table also shows hold two seats, not four."""
    assert room(_cap(), _held(2), _reading(2))['jmag'] == 2


def test_the_larger_of_held_and_observed_counts() -> None:
    assert room(_cap(), _held(1), _reading(3))['jmag'] == 1
    assert room(_cap(), _held(3), _reading(1))['jmag'] == 1


def test_the_seat_margin_is_never_lent() -> None:
    assert room(_cap(margin=2), [], _reading(0))['jmag'] == 2
    assert room(_cap(margin=2), [], _reading(1))['jmag'] == 1
    assert room(_cap(margin=2), [], _reading(3))['jmag'] == 0


def test_an_unreadable_observation_lends_nothing_and_says_why() -> None:
    capacity = _cap()
    assert room(capacity, [], _reading(None))['jmag'] == 0
    why = _decide(capacity, [], _reading(None), Demand({'cpu': 1, 'mem_gb': 1, 'jmag': 1}), 'forge jmag')
    assert why is not None
    assert 'process table could not be read' in why


def test_a_reading_that_never_observed_a_watched_seat_lends_nothing() -> None:
    """A reader that forgot the observation is unknown, never zero in use."""
    assert room(_cap(), [], Reading(8.0, 8.0, 16 * GB, 16 * GB))['jmag'] == 0


def test_a_blind_supply_probe_lends_nothing_and_the_refusal_says_why() -> None:
    capacity = Capacity(cpu=7.0, mem_bytes=12 * GB, seats={'jmag': 4}, blind={'jmag': 'its supply probe failed'})
    reading = Reading(8.0, 8.0, 16 * GB, 16 * GB)
    assert room(capacity, [], reading)['jmag'] == 0
    why = _decide(capacity, [], reading, Demand({'cpu': 1, 'mem_gb': 1, 'jmag': 1}), 'forge jmag')
    assert why is not None
    assert 'supply probe failed' in why


def test_an_unwatched_seat_stays_declared_less_held() -> None:
    assert room(_cap(watch=False), _held(1), Reading(8.0, 8.0, 16 * GB, 16 * GB))['jmag'] == 3


def test_observe_counts_solver_images_case_blind() -> None:
    rows = {1: ('JAM.EXE', 0), 2: ('jam_c.exe', 0), 3: ('designer.exe', 0), 4: ('jam.exe', 0)}
    assert observe(_JMAG, rows) == ({'jmag': 3}, {})


def test_observe_names_an_unreadable_table() -> None:
    seen, unseen = observe(_JMAG, None)
    assert seen == {}
    assert 'jmag' in unseen


def test_the_forge_box_supply_counts_the_same_seats() -> None:
    supply = box(
        'ws1',
        capacity=_cap(margin=2),
        read=lambda _pids: _reading(1),
        platform='win32',
        presence={'jmag': Presence('jmag', present=True)},
    )
    assert supply.resources['jmag'] == 1


def test_the_machine_file_spells_the_observation_and_the_margin() -> None:
    table = {'jmag': {'seats': 4, 'in_use': {'process': ['jam.exe', 'Jam_C.exe']}}, 'workstation': {}}
    assert watched(provided(table)) == {'jmag': ('jam.exe', 'jam_c.exe')}
    assert reserve_table({'seat_margin': {'jmag': 2}}).seat_margin == {'jmag': 2}


@pytest.mark.parametrize(
    'row',
    [{'in_use': {'process': []}}, {'in_use': {'process': 'jam.exe'}}, {'in_use': {'window': ['x']}}, {'in_use': 1}],
)
def test_a_malformed_observation_is_refused(row: dict[str, object]) -> None:
    with pytest.raises(ValueError, match='in_use'):
        provided({'jmag': row})


@pytest.mark.parametrize('margin', [{'jmag': -1}, {'jmag': 1.5}, {'Bad': 1}, 2])
def test_a_malformed_seat_margin_is_refused(margin: object) -> None:
    with pytest.raises(ValueError, match='seat_margin'):
        reserve_table({'seat_margin': margin})
