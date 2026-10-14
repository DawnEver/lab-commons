"""The box: one ledger of running claims per machine, admitted against the machine file's capacity."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import time
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

import pytest

from lab_commons.box import BOX_ENV, GRACE_S, Capacity, Demand, Holding, Refused, admit, fits, holdings

_CAP = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={'jmag': 1})


def _demand(cpu: int = 1, mem_gb: float = 1.0, wall_s: int = 60, seats: dict[str, int] | None = None) -> Demand:
    return Demand(cpu=cpu, mem_gb=mem_gb, wall_s=wall_s, seats=seats or {})


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    with admit(_demand(), 'schema', queue_s=1, key='k', capacity=_CAP):
        pass
    return path


def test_a_demand_without_a_field_is_a_type_error() -> None:
    missing: dict[str, Any] = {'cpu': 1, 'mem_gb': 1.0, 'wall_s': 60}
    with pytest.raises(TypeError):
        Demand(**missing)


@pytest.mark.parametrize(
    'fields', [{'cpu': 0}, {'mem_gb': 0.0}, {'wall_s': -1}, {'seats': {'jmag': -1}}], ids=['cpu', 'mem', 'wall', 'seat']
)
def test_a_non_positive_dimension_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=next(iter(fields)).split('_')[0]):
        Demand(**{'cpu': 1, 'mem_gb': 1.0, 'wall_s': 60, 'seats': {}, **fields})


def test_fits_names_the_dimension_and_its_holders() -> None:
    held = Holding(what='solver', pid=1, cpu=3, mem_bytes=2**30, seats={'jmag': 1}, deadline=1234.0)
    assert fits(_CAP, [held], _demand(cpu=1)) is None
    refusal = fits(_CAP, [held], _demand(seats={'jmag': 1}))
    assert refusal is not None
    assert 'jmag' in refusal
    assert 'solver' in refusal
    assert '1970-01-01T00:20:34' in refusal


@pytest.mark.usefixtures('box')
def test_an_oversize_demand_is_refused_at_once() -> None:
    start = time.monotonic()
    with pytest.raises(Refused, match='cpu'), admit(_demand(cpu=5), 'big', queue_s=30, key='k', capacity=_CAP):
        pass
    assert time.monotonic() - start < 5


@pytest.mark.usefixtures('box')
def test_an_undeclared_seat_never_fits() -> None:
    with (
        pytest.raises(Refused, match='matlab'),
        admit(_demand(seats={'matlab': 1}), 'm', queue_s=30, key='k', capacity=_CAP),
    ):
        pass


@pytest.mark.usefixtures('box')
def test_a_claim_is_a_row_while_held_and_gone_after() -> None:
    with admit(_demand(cpu=2, wall_s=100), 'run', queue_s=1, key='k', capacity=_CAP) as claim:
        rows = holdings()
        assert [row.what for row in rows] == ['run']
        assert rows[0].pid == os.getpid()
        assert rows[0].deadline == pytest.approx(claim.deadline, abs=0.0)  # the same float, read back
        assert claim.deadline - time.time() == pytest.approx(100, abs=5)
    assert holdings() == []


@pytest.mark.usefixtures('box')
def test_a_wait_never_exceeds_queue_s_and_names_the_holder() -> None:
    with admit(_demand(cpu=4), 'holder-run', queue_s=1, key='k', capacity=_CAP):
        start = time.monotonic()
        with (
            pytest.raises(Refused, match='holder-run'),
            admit(_demand(cpu=1), 'late', queue_s=1.0, key='k', capacity=_CAP),
        ):
            pass
        assert time.monotonic() - start < 1.0 + 1.0


def _ticket(path: Path, pid: int) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            'INSERT INTO tickets (pid, created, gives_up, what) VALUES (?, NULL, ?, ?)', (pid, time.time() + 600, 'w')
        )


def _row(path: Path, *, pid: int, created: int | None, deadline: float, cpu: int = 4) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            'INSERT INTO claims (pid, created, cpu, mem_bytes, seats, deadline, started, what, key) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (pid, created, cpu, 2**30, json.dumps({}), deadline, time.time(), 'ghost', 'ghost-key'),
        )


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    return child.pid


def test_a_ticket_holds_nothing(box: Path) -> None:
    _ticket(box, os.getpid())  # a live waiter, queued
    assert holdings() == []
    assert fits(_CAP, holdings(), _demand(cpu=4)) is None


def test_a_dead_waiters_ticket_does_not_block_the_queue(box: Path) -> None:
    _ticket(box, _dead_pid())
    with admit(_demand(), 'behind', queue_s=1, key='k', capacity=_CAP):
        assert [row.what for row in holdings()] == ['behind']


def test_a_dead_launchers_row_is_reaped(box: Path) -> None:
    _row(box, pid=_dead_pid(), created=None, deadline=time.time() + 600)
    _row(box, pid=os.getpid(), created=-1, deadline=time.time() + 600)  # a recycled pid: the stamp differs
    with admit(_demand(cpu=4), 'after', queue_s=1, key='k', capacity=_CAP):
        assert [row.what for row in holdings()] == ['after']


def test_an_expired_deadline_row_is_reaped(box: Path) -> None:
    _row(box, pid=os.getpid(), created=None, deadline=time.time() - GRACE_S - 1)
    with admit(_demand(cpu=4), 'after', queue_s=1, key='k', capacity=_CAP):
        assert [row.what for row in holdings()] == ['after']


def test_a_deadline_within_grace_still_holds(box: Path) -> None:
    _row(box, pid=os.getpid(), created=None, deadline=time.time() - 1)
    with pytest.raises(Refused, match='ghost'), admit(_demand(cpu=1), 'after', queue_s=0.5, key='k', capacity=_CAP):
        pass


def _race(path: str, peak: Queue[int]) -> None:
    os.environ[BOX_ENV] = path
    with admit(_demand(cpu=1, mem_gb=1.0), f'racer-{os.getpid()}', queue_s=60, key='k', capacity=_CAP, poll_s=0.02):
        rows = holdings()
        peak.put(sum(row.cpu for row in rows))
        time.sleep(0.1)


def test_racing_admits_never_exceed_capacity(box: Path) -> None:
    peak: Queue[int] = multiprocessing.Queue()
    racers = [multiprocessing.Process(target=_race, args=(str(box), peak)) for _ in range(10)]
    for racer in racers:
        racer.start()
    seen = [peak.get(timeout=120) for _ in racers]
    for racer in racers:
        racer.join(120)
    assert all(racer.exitcode == 0 for racer in racers)
    assert len(seen) == len(racers)
    assert max(seen) <= _CAP.cpu


def test_capacity_reads_the_reserve_margins_compute_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / 'config.toml'
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(config))
    config.write_text(
        '[reserve]\ncpu_margin = 1\nmem_margin = 0.25\n[capabilities.jmag]\nseats = 3\n', encoding='utf-8'
    )
    loose = Capacity.read()
    config.write_text('[reserve]\ncpu_margin = 2\nmem_margin = 0.5\n[capabilities.jmag]\nseats = 3\n', encoding='utf-8')
    tight = Capacity.read()
    assert loose.cpu - tight.cpu == pytest.approx(1, abs=1e-9)  # cores
    assert tight.mem_bytes < loose.mem_bytes
    assert loose.seats == {'jmag': 3}
    assert loose.cpu == pytest.approx((os.cpu_count() or 1) - 1, abs=1e-9)  # cores
