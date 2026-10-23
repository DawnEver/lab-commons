"""THE BOX'S SUPPLY IS MEASURED (D3): what the OS has free now, less what admitted claims have not yet taken.

A box shared with an editor, a vendor GUI or another user has less room than its size less its claims; a
claim that has not grown to its declaration yet still owns the rest. Each test drives :func:`room` with a
:class:`Reading` it builds, so the arithmetic is pinned without a loaded machine; the last one reads the OS.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, GB, Capacity, Demand, Holding, Refused, hold
from lab_commons.box.ledger import stuck_at_head
from lab_commons.box.supply import Reading, Usage, room
from lab_commons.proccpu import system_cpu_seconds

_CAP = Capacity(cpu=7.0, mem_bytes=12 * GB, seats={'jmag': 1}, kept_cpu=1.0, kept_bytes=4 * GB)  # 8 cores, 16 GB


def _quiet(**usage: Usage) -> Reading:
    """Nobody but the claims uses anything: idle and available are the whole box."""
    return Reading(cores=8.0, idle_cores=8.0, total_bytes=16 * GB, available_bytes=16 * GB, usage=dict(usage))


def _held(pid: int, *, cpu: int = 2, mem_gb: float = 4.0, seats: dict[str, int] | None = None) -> Holding:
    return Holding(what=f'run {pid}', pid=pid, cpu=cpu, mem_bytes=int(mem_gb * GB), seats=seats or {}, deadline=0.0)


def test_a_quiet_box_lends_its_size_less_what_is_declared() -> None:
    """PLANTED CONTROL: nothing else runs, so the measured room IS the declared one."""
    free = room(_CAP, [_held(1, seats={'jmag': 1})], _quiet())
    assert free['cpu'] == pytest.approx(5.0, abs=1e-9)  # cores
    assert free['mem_gb'] == pytest.approx(8.0, abs=1e-9)  # GB
    assert free['jmag'] == 0


def test_memory_another_user_holds_is_not_lent() -> None:
    """THE CHECK: a 6 GB editor nobody declared leaves 2 GB of the 8 a declared-sum ledger would lend."""
    busy = Reading(cores=8.0, idle_cores=8.0, total_bytes=16 * GB, available_bytes=10 * GB, usage={})
    free = room(_CAP, [_held(1)], busy)
    assert free['mem_gb'] == pytest.approx(2.0, abs=1e-9)  # GB: 10 available - 4 kept - 4 not yet resident


def test_a_resident_claim_owns_only_what_it_has_not_taken_yet() -> None:
    """A claim at 3 GB of its 4 is in the OS reading already; only its last 1 GB is subtracted again."""
    reading = Reading(
        cores=8.0, idle_cores=8.0, total_bytes=16 * GB, available_bytes=13 * GB, usage={1: Usage(3 * GB, 0.0)}
    )
    free = room(_CAP, [_held(1)], reading)
    assert free['mem_gb'] == pytest.approx(8.0, abs=1e-9)  # GB: 13 - 4 kept - 1 outstanding


def test_cores_busy_outside_the_ledger_are_not_lent_and_a_busy_claim_is_not_counted_twice() -> None:
    reading = Reading(cores=8.0, idle_cores=3.0, total_bytes=16 * GB, available_bytes=16 * GB, usage={1: Usage(0, 2.0)})
    free = room(_CAP, [_held(1, cpu=2)], reading)
    assert free['cpu'] == pytest.approx(2.0, abs=1e-9)  # cores: 3 idle - 1 kept - 0 outstanding


def test_a_noisy_reading_never_lends_more_than_the_size_less_the_declarations() -> None:
    """Idle sampled before a busy claim showed up: the measured 7 cores are capped at the declared 5."""
    reading = Reading(cores=8.0, idle_cores=8.0, total_bytes=16 * GB, available_bytes=16 * GB, usage={1: Usage(0, 2.0)})
    assert room(_CAP, [_held(1, cpu=2)], reading)['cpu'] == pytest.approx(5.0, abs=1e-9)  # cores


def test_the_ledger_refuses_a_demand_the_declared_sum_would_admit(tmp_path: Path, monkeypatch) -> None:
    """THE LEDGER DECIDES ON THE MEASURED ROOM, through the one rule, naming what is lacking."""
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    busy = Reading(cores=8.0, idle_cores=8.0, total_bytes=16 * GB, available_bytes=6 * GB, usage={})
    big = Demand(1, 4.0, 60, {})
    with (
        pytest.raises(Refused, match=r'lacking mem_gb \(4 asked, 2 free\)'),
        hold(big, 'big', 0.0, key='k', capacity=_CAP, read=lambda _pids: busy),
    ):
        pass
    with hold(Demand(1, 4.0, 60, {}), 'big', 0.0, key='k', capacity=_CAP, read=lambda _pids: _quiet()):
        pass


def test_this_box_reads_a_sane_reading() -> None:
    """The OS readers answer on this platform: cores, idle within them, available within total."""
    reading = Reading.now([os.getpid()], sample_s=0.05)
    assert reading.cores >= 1
    assert 0 <= reading.idle_cores <= reading.cores + 1e-6  # cores
    assert 0 < reading.available_bytes <= reading.total_bytes
    assert reading.usage[os.getpid()].resident_bytes > 0
    assert system_cpu_seconds() is not None


# ISSUE #5 (2026-10-19, five deadlocks): a HOLDER's nested ask (a vendor Designer it starts and hands over) is
# sequential with the holder's own growth -- the holder waits on its child -- so the holder's UNUSED part is
# room for its own nested claim. Measured: 10.0 GB free, holder 1.4 GB unused, 3.4 GB kept, nested ask 6.29 GB.
_ISSUE5_CAP = Capacity(cpu=7.0, mem_bytes=28 * GB, seats={}, kept_cpu=1.0, kept_bytes=int(3.4 * GB))
_ISSUE5_READ = Reading(
    cores=8.0, idle_cores=8.0, total_bytes=32 * GB, available_bytes=10 * GB, usage={7: Usage(int(2.6 * GB), 0.0)}
)
_NESTED_GB = 6.29


def test_a_holders_nested_ask_fits_only_because_its_own_unused_part_is_its_own() -> None:
    """PLANTED both ways: the same ask on the same box fits for the holder and not for a stranger."""
    holder = [_held(7, cpu=1, mem_gb=4.0)]  # 4 GB declared, 2.6 GB resident -> 1.4 GB unused
    assert room(_ISSUE5_CAP, holder, _ISSUE5_READ)['mem_gb'] < _NESTED_GB, 'a stranger still sees 5.2 GB'
    assert room(_ISSUE5_CAP, holder, _ISSUE5_READ, asker=7)['mem_gb'] >= _NESTED_GB  # 10 - 3.4 = 6.6 GB


def test_another_pids_unused_claim_is_still_counted_against_the_asker() -> None:
    held = [_held(7, cpu=1, mem_gb=4.0), _held(8, cpu=1, mem_gb=2.0)]  # pid 8 resident 0: 2 GB owed
    assert room(_ISSUE5_CAP, held, _ISSUE5_READ, asker=7)['mem_gb'] == pytest.approx(10 - 3.4 - 2.0, abs=1e-6)


def test_a_nested_ask_beyond_even_the_holders_headroom_still_does_not_fit() -> None:
    free = room(_ISSUE5_CAP, [_held(7, cpu=1, mem_gb=4.0)], _ISSUE5_READ, asker=7)['mem_gb']
    assert free < 7.0


def test_a_room_refused_head_ticket_no_deadline_frees_in_time_is_named_stuck() -> None:
    """PLANTED both ways: stuck when every holder outlives the ticket; not when one is due, nor behind FIFO."""
    late = [Holding(what='a', pid=1, cpu=1, mem_bytes=GB, seats={}, deadline=200.0)]
    soon = [*late, Holding(what='b', pid=2, cpu=1, mem_bytes=GB, seats={}, deadline=50.0)]
    assert stuck_at_head('mem_gb lacking', late, gives_up=100.0)
    assert not stuck_at_head('mem_gb lacking', soon, gives_up=100.0)
    assert not stuck_at_head("queued behind 'x', which came first", late, gives_up=100.0)
