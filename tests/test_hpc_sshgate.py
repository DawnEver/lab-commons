"""Every hpc ssh shares a connection and is rate-limited machine-wide (user ruling 2026-11-11)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from lab_commons.hpc import cluster
from lab_commons.hpc.config import POLL_FLOOR_S, Policy
from lab_commons.hpc.sshgate import LIMITS, Gate, Limits, RateLimited, limits_of

HOST = 'alice@hpclogin01.example.org'


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        """Start at an arbitrary epoch second."""
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        """A sleep that moves this clock, recorded so a test can read the wait."""
        self.slept = [*getattr(self, 'slept', []), seconds]
        self.now += seconds


def _gate(root: Path, clock: Clock, platform: str = 'linux', limits: Limits = LIMITS) -> Gate:
    return Gate(HOST, root=root, limits=limits, platform=platform, clock=clock, sleep=clock.sleep)


def _enter(gate: Gate) -> None:
    with gate.call():
        pass


def test_a_second_session_inside_the_interval_waits_it_out_in_the_door(tmp_path: Path) -> None:
    """The interval PACES a pipeline's back-to-back doors; it never refuses them (the window cap does)."""
    clock = Clock()
    _enter(_gate(tmp_path, clock))
    clock.now += 2
    _enter(_gate(tmp_path, clock))
    assert clock.slept == [3.0]  # the planted control: inside the interval, it waited exactly the remainder
    clock.now += 10
    _enter(_gate(tmp_path, clock))
    assert clock.slept == [3.0]  # outside it, no wait


def test_the_window_refusal_names_the_wait_and_the_doors(tmp_path: Path) -> None:
    clock = Clock()
    limits = Limits(min_interval_s=0, max_per_window=1, window_s=60)
    _enter(_gate(tmp_path, clock, limits=limits))
    with pytest.raises(RateLimited, match=r'allowed in 60s') as refused:
        _enter(_gate(tmp_path, clock, limits=limits))
    assert 'forge queue' in str(refused.value)
    assert 'gather -c <job> --wait' in str(refused.value)


def test_one_runner_takes_one_slot_however_many_commands(tmp_path: Path) -> None:
    gate = _gate(tmp_path, Clock())
    for _ in range(5):
        _enter(gate)


def test_the_window_caps_sessions(tmp_path: Path) -> None:
    clock = Clock()
    limits = Limits(min_interval_s=0, max_per_window=2, window_s=60)
    _enter(_gate(tmp_path, clock, limits=limits))
    _enter(_gate(tmp_path, clock, limits=limits))
    with pytest.raises(RateLimited, match='2/2'):
        _enter(_gate(tmp_path, clock, limits=limits))
    clock.now += 60
    _enter(_gate(tmp_path, clock, limits=limits))


def test_posix_shares_one_controlmaster_keyed_by_a_hash(tmp_path: Path) -> None:
    options = _gate(tmp_path, Clock()).options()
    assert 'ControlMaster=auto' in options
    assert 'ControlPersist=10m' in options
    path = next(o for o in options if o.startswith('ControlPath='))
    assert path.startswith(f'ControlPath={tmp_path.as_posix()}/')
    assert 'hpclogin01' not in path


def test_windows_cannot_multiplex_so_it_names_no_control_path(tmp_path: Path) -> None:
    assert _gate(tmp_path, Clock(), platform='win32').options() == []


def test_windows_serializes_one_live_ssh_per_host(tmp_path: Path) -> None:
    """PLANTED CONTROL: a second call waits while the first holds the host lock."""
    limits = Limits(min_interval_s=0)
    first, second = (_gate(tmp_path, Clock(), 'win32', limits) for _ in range(2))
    order: list[str] = []
    inside = threading.Event()

    def hold() -> None:
        with first.call():
            inside.set()
            time.sleep(0.5)
            order.append('first out')

    def follow() -> None:
        with second.call():
            order.append('second in')

    a = threading.Thread(target=hold)
    a.start()
    inside.wait()
    b = threading.Thread(target=follow)
    b.start()
    a.join()
    b.join()
    assert order == ['first out', 'second in']


def test_the_ssh_runner_carries_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cluster, '_completed', lambda argv, _s, _t: seen.append(argv) or 'ok')
    clock = Clock()
    one = Limits(min_interval_s=0, max_per_window=1, window_s=60)
    run = cluster.ssh_runner(HOST, gate=_gate(tmp_path, clock, limits=one))
    assert run('true', None) == 'ok'
    assert 'ControlMaster=auto' in seen[0]
    with pytest.raises(RateLimited):
        cluster.ssh_runner(HOST, gate=_gate(tmp_path, clock, limits=one))('true', None)
    assert len(seen) == 1


def test_the_machine_file_overrides_the_limits_and_refuses_unknown_keys() -> None:
    assert limits_of({'ssh': {'min_interval_s': 9}}) == Limits(min_interval_s=9)
    assert limits_of({}) == LIMITS
    with pytest.raises(ValueError, match='unknown keys'):
        limits_of({'ssh': {'burst': 3}})


def test_a_poll_below_the_floor_is_refused() -> None:
    Policy(poll_seconds=POLL_FLOOR_S)
    with pytest.raises(ValueError, match='unusable policy'):
        Policy(poll_seconds=POLL_FLOOR_S - 1)
