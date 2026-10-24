"""The box: one ledger of running claims per machine, held when the one rule admits them on its room."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

import pytest
from _box_fake import quiet

from lab_commons.box import (
    BOX_ENV,
    GRACE_S,
    INSIDE_ENV,
    Capacity,
    DuplicateKey,
    Holding,
    NotAdmitted,
    Refused,
    hand_over,
    hold,
    holdings,
    not_admitted,
    persist,
    release,
)
from lab_commons.box.demand import checked, cores, mem_bytes, seats
from lab_commons.box.ledger import _decide
from lab_commons.box.supply import Reading
from lab_commons.compute.model import Demand

_CAP = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={'jmag': 1})
_QUIET = quiet(_CAP)
_ON: dict[str, Any] = {'key': 'k', 'capacity': _CAP, 'read': _QUIET}


def _demand(cpu: int = 1, mem_gb: float = 1.0, wall_s: int = 60, seats: dict[str, int] | None = None) -> Demand:
    return Demand({**(seats or {}), 'cpu': cpu, 'mem_gb': mem_gb}), wall_s


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    with hold(*_demand(), 'schema', queue_s=1, **_ON):
        pass
    return path


@pytest.mark.parametrize(
    ('demand', 'wall_s', 'named'),
    [
        ({'cpu': -1, 'mem_gb': 1.0}, 60, 'negative'),
        ({'cpu': 1.5, 'mem_gb': 1.0}, 60, 'cpu'),
        ({'cpu': 1, 'mem_gb': 0.0}, 60, 'mem_gb'),
        ({'cpu': 1, 'mem_gb': 1.0}, -1, 'wall_s'),
        ({'cpu': 1, 'mem_gb': 1.0, 'jmag': 0.5}, 60, 'jmag'),
    ],
    ids=['cpu', 'whole-cpu', 'mem', 'wall', 'seat'],
)
def test_a_bad_dimension_is_refused_by_name(demand: dict[str, float], wall_s: int, named: str) -> None:
    with pytest.raises(ValueError, match=named):
        checked(demand, wall_s)


def test_a_claim_may_book_memory_without_a_core() -> None:
    """A process that holds memory and a seat but runs no solver -- a vendor GUI preparing a job -- asks 0 cores."""
    assert cores(checked({'jmag': 1, 'cpu': 0, 'mem_gb': 1.5}, 60)) == 0
    assert checked({'cpu': 0, 'mem_gb': 1.5}, 60)['mem_gb'] == 1.5


def test_a_demand_for_nothing_is_still_refused() -> None:
    """PLANTED CONTROL: zero cores AND zero memory with no seat books nothing, so it is no claim at all."""
    with pytest.raises(ValueError, match='book something'):
        checked(Demand(cpu=0, mem_gb=0.0), 60)


@pytest.mark.usefixtures('box')
def test_a_coreless_claim_is_admitted_while_another_claim_holds_every_core() -> None:
    """D10 (motronics, 2026-10-23): a held solver claim never queues a stage that asks no core."""
    with (
        hold(*_demand(cpu=4, mem_gb=1.0), 'solver', queue_s=0.2, **_ON),
        hold(*_demand(cpu=0, mem_gb=1.0, seats={'jmag': 1}), 'prepare', queue_s=0.2, **{**_ON, 'key': 'p'}),
    ):
        assert sorted(row.what for row in holdings()) == ['prepare', 'solver']


def test_a_coreful_claim_still_waits_for_held_cores(box: Path) -> None:
    """PLANTED CONTROL: the same claim asking one core is refused while the solver holds all four."""
    # ANOTHER process holds (the parent, alive): this process's own unused claim would be its own room (issue #5)
    _row(box, pid=os.getppid(), created=None, deadline=time.time() + 600)
    with (
        pytest.raises(NotAdmitted),
        hold(*_demand(cpu=1, mem_gb=1.0), 'prepare', queue_s=0.2, **{**_ON, 'key': 'p'}),
    ):
        pass


def test_a_refusal_names_what_is_lacking_and_its_holders() -> None:
    held = Holding(what='solver', pid=1, cpu=3, mem_bytes=2**30, seats={'jmag': 1}, deadline=1234.0)
    assert _decide(_CAP, [held], _QUIET(()), _demand(cpu=1), 'w') is None
    refusal = _decide(_CAP, [held], _QUIET(()), _demand(seats={'jmag': 1}), 'w')
    assert refusal is not None
    assert 'jmag' in refusal
    assert 'solver' in refusal
    assert '1970-01-01T00:20:34' in refusal


@pytest.mark.usefixtures('box')
def test_an_oversize_demand_is_refused_at_once() -> None:
    start = time.monotonic()
    with (
        pytest.raises(Refused, match='cpu'),
        hold(*_demand(cpu=5), 'big', queue_s=30, **_ON),
    ):
        pass
    assert time.monotonic() - start < 5


@pytest.mark.usefixtures('box')
def test_an_undeclared_seat_counts_one() -> None:
    """A seat nobody declared counts 1, as a present undeclared tool does: one holder, and the next waits."""
    with (
        hold(*_demand(seats={'matlab': 1}), 'm', queue_s=1, **_ON),
        pytest.raises(Refused, match=r'lacking matlab .* m \(pid'),
        hold(*_demand(seats={'matlab': 1}), 'n', queue_s=0.2, **_ON),
    ):
        pass


@pytest.mark.usefixtures('box')
def test_a_claim_is_a_row_while_held_and_gone_after() -> None:
    with hold(*_demand(cpu=2, wall_s=100), 'run', queue_s=1, **_ON) as claim:
        rows = holdings()
        assert [row.what for row in rows] == ['run']
        assert rows[0].pid == os.getpid()
        assert rows[0].deadline == pytest.approx(claim.deadline, abs=0.0)  # the same float, read back
        assert claim.deadline - time.time() == pytest.approx(100, abs=5)
    assert holdings() == []


@pytest.mark.usefixtures('box')
def test_a_wait_never_exceeds_queue_s_and_names_the_holder(box: Path) -> None:
    # ANOTHER process holds (the parent, alive): this process's own unused claim would be its own room (issue #5)
    _row(box, pid=os.getppid(), created=None, deadline=time.time() + 600)
    start = time.monotonic()
    with (
        pytest.raises(Refused, match='ghost'),
        hold(*_demand(cpu=1), 'late', queue_s=1.0, **_ON),
    ):
        pass
    assert time.monotonic() - start < 1.0 + 1.0


def test_the_holders_own_nested_ask_is_admitted_from_its_own_unused_claim() -> None:
    """ISSUE #5 through the ledger: a holder's nested hold fits its own headroom."""
    with (
        hold(*_demand(cpu=4), 'holder-run', queue_s=1, **_ON),
        hold(*_demand(cpu=1), 'its designer', queue_s=1.0, outside=True, **_ON),
    ):
        pass


def _ticket(path: Path, pid: int, demand: Demand | None = None) -> None:
    """A waiter's ticket; with no *demand* it is a ticket written before tickets declared one: it contends for all."""
    asked = (None, None, None) if demand is None else (cores(demand), mem_bytes(demand), json.dumps(seats(demand)))
    with sqlite3.connect(path) as con:
        con.execute(
            'INSERT INTO tickets (pid, created, gives_up, what, cpu, mem_bytes, seats) VALUES (?, NULL, ?, ?, ?, ?, ?)',
            (pid, time.time() + 600, 'w', *asked),
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
    assert _decide(_CAP, holdings(), _QUIET(()), _demand(cpu=4)[0], 'w') is None


def test_a_dead_waiters_ticket_does_not_block_the_queue(box: Path) -> None:
    _ticket(box, _dead_pid())
    with hold(*_demand(), 'behind', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['behind']


def test_a_dead_launchers_row_is_reaped(box: Path) -> None:
    _row(box, pid=_dead_pid(), created=None, deadline=time.time() + 600)
    _row(box, pid=os.getpid(), created=-1, deadline=time.time() + 600)  # a recycled pid: the stamp differs
    with hold(*_demand(cpu=4), 'after', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['after']


def test_an_expired_deadline_row_is_reaped(box: Path) -> None:
    _row(box, pid=os.getpid(), created=None, deadline=time.time() - GRACE_S - 1)
    with hold(*_demand(cpu=4), 'after', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['after']


def test_a_deadline_within_grace_still_holds(box: Path) -> None:
    _row(box, pid=os.getppid(), created=None, deadline=time.time() - 1)
    with (
        pytest.raises(Refused, match='ghost'),
        hold(*_demand(cpu=1), 'after', queue_s=0.5, **_ON),
    ):
        pass


def _race(path: str, peak: Queue[int]) -> None:
    os.environ[BOX_ENV] = path
    with hold(*_demand(cpu=1, mem_gb=1.0), f'racer-{os.getpid()}', queue_s=60, **_ON, poll_s=0.02):
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


def _external_rows(path: Path) -> list[tuple[int, int]]:
    with sqlite3.connect(path) as con:
        return con.execute('SELECT pid, kept FROM claims').fetchall()


def test_a_claim_handed_to_a_vendor_lives_and_is_reaped_with_it(box: Path) -> None:
    """hand_over: the vendor process holds the claim after the launcher's body ends; its death frees it."""
    vendor = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        with hold(*_demand(cpu=2), 'designer', queue_s=1, **_ON) as claim:
            hand_over(claim, vendor.pid)
        assert _external_rows(box) == [(vendor.pid, 1)], 'the body ended; the vendor still holds it'
    finally:
        vendor.kill()
        vendor.wait(30)
    with hold(*_demand(cpu=4), 'after', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['after'], 'PLANTED CONTROL: a dead vendor holds nothing'


def test_an_external_claim_outlives_its_client_until_its_probe_says_the_job_is_dead(box: Path, tmp_path: Path) -> None:
    """persist: no process of ours holds it; the reaper asks the probe once the client is gone."""
    flag = tmp_path / 'job-alive'
    flag.write_text('', encoding='utf-8')
    probe = [sys.executable, '-c', f'import pathlib, sys; sys.exit(0 if pathlib.Path({str(flag)!r}).exists() else 1)']
    client = (
        'import os, sys; from lab_commons.box import Capacity, hold, persist; '
        'from lab_commons.compute.model import Demand; '
        'from lab_commons.box.supply import Reading; '
        f'os.environ["{BOX_ENV}"] = {str(box)!r}; c = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={{}}); '
        'r = Reading(4.0, 4.0, 8 * 2**30, 8 * 2**30); '
        'cm = hold(Demand(cpu=2, mem_gb=1.0), 600, "scheduler job", 1, key="job", capacity=c, read=lambda _p: r); '
        f'claim = cm.__enter__(); persist(claim, {probe!r}); cm.__exit__(None, None, None)'
    )
    subprocess.run([sys.executable, '-c', client], check=True, timeout=120)
    with hold(*_demand(cpu=1), 'peer', queue_s=1, **_ON):
        assert sorted(row.what for row in holdings()) == ['peer', 'scheduler job'], 'its client died; the job lives'
    flag.unlink()
    with hold(*_demand(cpu=4), 'after', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['after'], 'the probe says the job is gone: reaped'


def test_released_on_terminal_evidence(box: Path) -> None:
    with hold(*_demand(cpu=2), 'job', queue_s=1, **_ON) as claim:
        persist(claim, [sys.executable, '-c', 'pass'])
    assert len(_external_rows(box)) == 1
    release(claim)
    assert holdings() == []


@pytest.mark.usefixtures('box')
def test_a_tree_inside_a_claim_does_not_claim_again_but_a_vendor_or_a_seat_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(INSIDE_ENV, 'outer')
    with hold(*_demand(cpu=4), 'nested', queue_s=1, **_ON) as claim:
        assert claim.id == 0
        assert holdings() == [], 'the enclosing claim already holds this tree'
    with hold(*_demand(cpu=1), 'vendor', queue_s=1, outside=True, **_ON):
        assert [row.what for row in holdings()] == ['vendor'], 'PLANTED CONTROL: outside the tree books'
    with hold(*_demand(seats={'jmag': 1}), 'seat', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['seat'], 'a licence is not a share of a tree'


def test_a_ledger_file_from_before_the_kept_column_moves_forward(tmp_path: Path, monkeypatch) -> None:
    old = tmp_path / 'old.sqlite'
    with sqlite3.connect(old) as con:
        con.execute(
            'CREATE TABLE claims (id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER, '
            'cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL, deadline REAL NOT NULL, '
            'started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT)'
        )
    monkeypatch.setenv(BOX_ENV, str(old))
    with hold(*_demand(), 'new', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['new']


def _external_client(box: Path, what: str, key: str, probe: list[str], seats: dict[str, int] | None = None) -> None:
    """A client in ANOTHER process holds *what* under *key*, persists it with *probe*, and exits."""
    client = (
        'import os; from lab_commons.box import Capacity, hold, persist; '
        'from lab_commons.compute.model import Demand; '
        'from lab_commons.box.supply import Reading; '
        f'os.environ["{BOX_ENV}"] = {str(box)!r}; c = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={{"jmag": 1}}); '
        'r = Reading(4.0, 4.0, 8 * 2**30, 8 * 2**30); '
        f'cm = hold(Demand({seats or {}!r}, cpu=1, mem_gb=1.0), 600, {what!r}, 1, key={key!r}, capacity=c, '
        'read=lambda _p: r); '
        f'claim = cm.__enter__(); persist(claim, {probe!r}); cm.__exit__(None, None, None)'
    )
    subprocess.run([sys.executable, '-c', client], check=True, timeout=120)


_ALIVE = [sys.executable, '-c', 'pass']
_DEAD = [sys.executable, '-c', 'import sys; sys.exit(1)']


def test_a_holding_carries_its_id_key_and_probe(box: Path) -> None:
    _external_client(box, 'study', 'proj/study-1', _ALIVE)
    (row,) = holdings()
    assert (row.key, row.probe) == ('proj/study-1', tuple(_ALIVE))
    assert row.id > 0
    assert row.claim.id == row.id


def test_another_process_releases_an_external_claim_by_its_id(box: Path) -> None:
    _external_client(box, 'study', 'proj/study-1', _ALIVE)
    (row,) = holdings()
    release(row.claim)
    assert holdings() == []


def test_a_live_external_key_refuses_a_duplicate_and_admits_another(box: Path) -> None:
    _external_client(box, 'study', 'proj/study-1', _ALIVE)
    with (
        pytest.raises(DuplicateKey, match=r'proj/study-1.*unresolved'),
        hold(*_demand(), 'again', queue_s=1, **{**_ON, 'key': 'proj/study-1'}),
    ):
        pass
    with hold(*_demand(), 'other', queue_s=1, **{**_ON, 'key': 'proj/study-2'}):
        assert sorted(r.what for r in holdings()) == ['other', 'study'], 'PLANTED CONTROL: a different key is admitted'


@pytest.mark.usefixtures('box')
def test_a_running_non_external_key_is_no_duplicate() -> None:
    with hold(*_demand(), 'a', queue_s=1, **_ON), hold(*_demand(), 'b', queue_s=1, **_ON):
        assert len(holdings()) == 2, 'only an EXTERNAL claim makes its key exclusive'


def test_a_dead_probe_frees_the_key_and_the_seat(box: Path) -> None:
    _external_client(box, 'study', 'proj/study-1', _DEAD, seats={'jmag': 1})
    with hold(*_demand(seats={'jmag': 1}), 'again', queue_s=1, **{**_ON, 'key': 'proj/study-1'}):
        assert [r.what for r in holdings()] == ['again']


def test_an_external_seat_is_held_until_released(box: Path) -> None:
    _external_client(box, 'study', 'proj/study-1', _ALIVE, seats={'jmag': 1})
    with (
        pytest.raises(Refused, match='jmag'),
        hold(*_demand(seats={'jmag': 1}), 'second', queue_s=0.2, **{**_ON, 'key': 'x'}),
    ):
        pass
    (row,) = holdings()
    release(row.claim)
    with hold(*_demand(seats={'jmag': 1}), 'second', queue_s=1, **{**_ON, 'key': 'x'}):
        assert [r.what for r in holdings()] == ['second']


@pytest.mark.usefixtures('box')
def test_a_queue_wait_that_runs_out_is_not_admitted_and_an_oversize_demand_is_not() -> None:
    """PLANTED CONTROL: waiting out the queue is CAPACITY (retry later); never fitting is a real refusal."""
    with (
        hold(*_demand(seats={'matlab': 1}), 'm', queue_s=1, **_ON),
        pytest.raises(NotAdmitted) as waited,
        hold(*_demand(seats={'matlab': 1}), 'n', queue_s=0.2, **_ON),
    ):
        pass
    assert not_admitted(f'dep --bootstrap exited 1: REFUSED: {waited.value}')
    with pytest.raises(Refused) as never, hold(*_demand(cpu=5), 'big', queue_s=30, **_ON):
        pass
    assert not isinstance(never.value, NotAdmitted)
    assert not not_admitted(str(never.value))
    assert not not_admitted('dep --bootstrap exited 1: no lock')


def test_a_demand_that_contends_for_nothing_ahead_passes_the_queue(box: Path) -> None:
    """PLANTED (live 2026-10-17): a zero-cpu env check queued behind a CPU-heavy verify and was refused."""
    _ticket(box, os.getpid(), _demand(cpu=4)[0])
    with hold(*_demand(cpu=0, mem_gb=0.0, seats={'env-x': 1}), 'dep:x', queue_s=0.2, **_ON):
        assert [row.what for row in holdings()] == ['dep:x']


def test_a_later_cpu_demand_that_would_delay_an_earlier_fitting_one_waits(box: Path) -> None:
    """PLANTED CONTROL: the earlier 4-core ticket fits now; 1 core held moves its start to that deadline."""
    _ticket(box, os.getpid(), _demand(cpu=4)[0])
    with (
        pytest.raises(NotAdmitted, match='queued behind'),
        hold(*_demand(cpu=1), 'late', queue_s=0.2, **_ON),
    ):
        pass


def test_a_coreless_stage_that_leaves_an_earlier_waiter_room_is_admitted(box: Path) -> None:
    """D10: a coreless 2 GB stage held beside a 4-core 4 GB waiter leaves its start now (8 GB holds both)."""
    _ticket(box, os.getpid(), _demand(cpu=4, mem_gb=4.0)[0])
    with hold(*_demand(cpu=0, mem_gb=2.0, seats={'jmag': 1}), 'prepare', queue_s=0.2, **_ON):
        assert [row.what for row in holdings()] == ['prepare']


def test_a_later_stage_that_would_delay_an_earlier_waiter_waits(box: Path) -> None:
    """PLANTED CONTROL: 5 GB held leaves the 4 GB waiter 3 GB: its start would move later, so the stage queues."""
    _ticket(box, os.getpid(), _demand(cpu=4, mem_gb=4.0)[0])
    with (
        pytest.raises(NotAdmitted, match='queued behind'),
        hold(*_demand(cpu=0, mem_gb=5.0, seats={'jmag': 1}), 'prepare', queue_s=0.2, **_ON),
    ):
        pass


def test_a_seat_demand_queues_behind_an_earlier_claim_for_the_same_seat(box: Path) -> None:
    """PLANTED CONTROL: contention is per resource; a seat is one."""
    _ticket(box, os.getpid(), _demand(cpu=0, mem_gb=0.0, seats={'jmag': 1})[0])
    with (
        pytest.raises(NotAdmitted, match='queued behind'),
        hold(*_demand(cpu=0, mem_gb=0.0, seats={'jmag': 1}), 'late', queue_s=0.2, **_ON),
    ):
        pass
    with hold(*_demand(cpu=0, mem_gb=0.0, seats={'env-y': 1}), 'other-seat', queue_s=0.2, **_ON):
        pass


def test_a_ticket_that_declares_no_demand_contends_for_everything(box: Path) -> None:
    """A ticket written before tickets declared their demand is ordered ahead of every later one, as before."""
    _ticket(box, os.getpid())
    with (
        pytest.raises(NotAdmitted),
        hold(*_demand(cpu=0, mem_gb=0.0, seats={'env-x': 1}), 'dep:x', queue_s=0.2, **_ON),
    ):
        pass


def _foreign(available_gb: float) -> Callable[[Iterable[int]], Reading]:
    """An OS reader of a box whose memory outside the ledger leaves only *available_gb* available."""
    return lambda _pids: Reading(4.0, 4.0, 8 * 2**30, int(available_gb * 2**30))


def test_a_waiter_the_foreign_load_makes_unreachable_has_no_start_to_protect(box: Path) -> None:
    """MEASURED 2026-10-23: a 45 GB measure under two foreign solves held the head for its whole wait.

    5 GB of the 8 are in use OUTSIDE the ledger, so no release here admits the 6 GB waiter: its earliest start is
    never, nothing can delay it, and a later 1 GB demand is admitted instead of idling the box behind it.
    """
    _ticket(box, os.getpid(), _demand(cpu=1, mem_gb=6.0)[0])
    with hold(*_demand(cpu=1, mem_gb=1.0), 'small', queue_s=0.2, **{**_ON, 'read': _foreign(3.0)}):
        assert [row.what for row in holdings()] == ['small']


def test_a_waiter_the_box_can_reach_is_not_delayed_by_a_later_demand(box: Path) -> None:
    """PLANTED CONTROL: with no foreign load the waiter fits now; 3 GB held would delay it, so the demand queues."""
    _ticket(box, os.getpid(), _demand(cpu=1, mem_gb=6.0)[0])
    with (
        pytest.raises(NotAdmitted, match='queued behind'),
        hold(*_demand(cpu=1, mem_gb=3.0), 'small', queue_s=0.2, **{**_ON, 'read': _foreign(8.0)}),
    ):
        pass
