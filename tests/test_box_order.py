"""The box queue: a ticket that fits is admitted past one that does not, unless that delays the earlier start.

PLANTED (live 2026-10-23, a consumer's long run): a 6.45 GB ``case run`` ticket that could not fit headed the
queue for hours, and three 1.74 GB vendor reads behind it -- each fitting the free room -- waited the whole time.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from _box_fake import quiet

from lab_commons.box import BOX_ENV, Capacity, NotAdmitted, hold, holdings
from lab_commons.box.demand import cores, mem_bytes, seats
from lab_commons.compute.model import Demand

_CAP = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={'jmag': 1})
_ON: dict[str, Any] = {'key': 'k', 'capacity': _CAP, 'read': quiet(_CAP)}
_HOLDER_S = 600.0


def _demand(cpu: int = 1, mem_gb: float = 1.0, wall_s: int = 60, seats: dict[str, int] | None = None) -> Demand:
    return Demand({**(seats or {}), 'cpu': cpu, 'mem_gb': mem_gb}), wall_s


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    with hold(*_demand(), 'schema', queue_s=1, **_ON):
        pass
    return path


@pytest.fixture
def other() -> Iterator[int]:
    """A live process that is not this one: the pid an other session's claim or ticket is planted under."""
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    yield child.pid
    child.kill()
    child.wait()


def _claim(path: Path, pid: int, mem_gb: float, ends_in: float) -> int:
    with sqlite3.connect(path) as con:
        return int(
            con.execute(
                'INSERT INTO claims (pid, created, cpu, mem_bytes, seats, deadline, started, what, key) '
                'VALUES (?,NULL,1,?,?,?,?,?,?)',
                (pid, int(mem_gb * 2**30), json.dumps({}), time.time() + ends_in, time.time(), 'holder', 'h'),
            ).lastrowid
            or 0
        )


def _ticket(path: Path, pid: int, demand: Demand, what: str = 'big') -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            'INSERT INTO tickets (pid, created, gives_up, what, cpu, mem_bytes, seats) VALUES (?, NULL, ?, ?, ?, ?, ?)',
            (pid, time.time() + 600, what, cores(demand), mem_bytes(demand), json.dumps(seats(demand))),
        )


def test_a_small_ticket_behind_an_unfit_big_one_is_admitted_while_it_fits(box: Path, other: int) -> None:
    _claim(box, other, mem_gb=5.0, ends_in=_HOLDER_S)  # 3 GB left: the big one cannot start before it ends
    _ticket(box, other, _demand(mem_gb=4.0)[0])
    with hold(*_demand(mem_gb=1.5, wall_s=60), 'read', queue_s=0.2, **_ON):
        assert sorted(row.what for row in holdings()) == ['holder', 'read']


def test_a_ticket_that_would_delay_the_earlier_start_waits(box: Path, other: int) -> None:
    """PLANTED CONTROL, and the starvation bound: a jumper must end by the earlier ticket's earliest start."""
    _claim(box, other, mem_gb=5.0, ends_in=_HOLDER_S)
    _ticket(box, other, _demand(mem_gb=6.0)[0])  # starts when the holder ends, unless 2.5 GB is still held then
    with hold(*_demand(mem_gb=2.5, wall_s=60), 'short', queue_s=0.2, **_ON):
        pass
    with (
        pytest.raises(NotAdmitted, match="queued behind 'big'"),
        hold(*_demand(mem_gb=2.5, wall_s=int(_HOLDER_S * 2)), 'long', queue_s=0.2, **_ON),
    ):
        pass


def test_a_stream_of_small_tickets_never_starves_the_big_one(box: Path, other: int) -> None:
    """Every jumper ends by the holder's deadline, so when the holder leaves the big one fits -- a bounded wait."""
    holder = _claim(box, other, mem_gb=5.0, ends_in=_HOLDER_S)
    big = _demand(mem_gb=4.0)
    _ticket(box, other, big[0])
    with hold(*_demand(mem_gb=1.0, wall_s=30), 'small-1', queue_s=0.2, **_ON):
        with hold(*_demand(mem_gb=1.0, wall_s=int(_HOLDER_S / 2)), 'small-2', queue_s=0.2, **_ON):
            ends = [h.deadline for h in holdings() if h.what.startswith('small')]
            assert max(ends) <= time.time() + _HOLDER_S
        with sqlite3.connect(box) as con:
            con.execute('DELETE FROM claims WHERE id = ?', (holder,))
            con.execute("DELETE FROM tickets WHERE what = 'big'")
        with hold(*big, 'big-now', queue_s=0.2, **_ON):  # the room the smalls leave holds it at once
            assert 'big-now' in [h.what for h in holdings()]


def test_a_holder_never_waits_behind_a_waiter_that_needs_its_memory(box: Path) -> None:
    """PLANTED (2026-10-19 deadlock): a claim holder's nested request passes an earlier waiter it blocks."""
    code = (
        'import time\n'
        'from _box_fake import quiet\n'
        'from lab_commons.box import Capacity, hold\n'
        'from lab_commons.compute.model import Demand\n'
        "cap = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={'jmag': 1})\n"
        "on = {'key': 'k', 'capacity': cap, 'read': quiet(cap)}\n"
        "with hold(Demand(cpu=1, mem_gb=5.0), 600, 'sweep', 1, **on):\n"
        "    print('held', flush=True)\n"
        '    input()\n'
        "    with hold(Demand(cpu=1, mem_gb=1.0), 3600, 'nested', 2, **on):\n"
        "        print('nested', flush=True)\n"
    )
    tests = str(Path(__file__).parent)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([tests, os.environ.get('PYTHONPATH', '')])}
    child = subprocess.Popen(
        [sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert child.stdout is not None
        assert child.stdin is not None
        assert child.stdout.readline().strip() == 'held'
        _ticket(box, os.getpid(), _demand(mem_gb=4.0)[0])  # waits on the sweep's memory
        child.stdin.write('\n')
        child.stdin.flush()
        assert child.stdout.readline().strip() == 'nested'
    finally:
        child.kill()
        child.wait()


def test_a_seat_only_ticket_is_untouched_by_memory_contention(box: Path, other: int) -> None:
    _claim(box, other, mem_gb=5.0, ends_in=_HOLDER_S)
    _ticket(box, other, _demand(mem_gb=4.0)[0])
    seat = Demand({'jobslot:n1': 1, 'cpu': 0, 'mem_gb': 0.0}), int(_HOLDER_S * 2)
    with hold(*seat, 'slot', queue_s=0.2, **_ON):
        assert 'slot' in [h.what for h in holdings()]
    _ticket(box, other, Demand({'jobslot:n1': 1}), what='slot-first')
    with (
        pytest.raises(NotAdmitted, match="queued behind 'slot-first'"),
        hold(*seat, 'slot-late', queue_s=0.2, **_ON),
    ):
        pass


def test_a_seat_holder_queues_behind_a_head_its_memory_would_delay(box: Path, other: int) -> None:
    """PLANTED (2026-10-28 live): a process holding only a seat is no holder of the head's memory.

    A gate measure held a 0-memory seat claim, then asked 8.1 GB; the holder exemption let five measures in a row
    pass a JMAG ticket waiting on memory. When the long holder ends early, the head goes in before any arrival.
    """
    holder = _claim(box, other, mem_gb=4.0, ends_in=_HOLDER_S)  # a 12 h-style deadline that ends early
    _ticket(box, other, _demand(mem_gb=6.0)[0])
    seat = Demand({'jmag': 1, 'cpu': 0, 'mem_gb': 0.0}), int(_HOLDER_S * 2)
    with hold(*seat, 'seat', queue_s=0.2, **_ON):
        with (
            pytest.raises(NotAdmitted, match="queued behind 'big'"),
            hold(*_demand(mem_gb=3.0, wall_s=int(_HOLDER_S * 2)), 'measure', queue_s=0.2, **_ON),
        ):
            pass
        with sqlite3.connect(box) as con:
            con.execute('DELETE FROM claims WHERE id = ?', (holder,))
        with (
            pytest.raises(NotAdmitted, match="queued behind 'big'"),
            hold(*_demand(mem_gb=6.0, wall_s=int(_HOLDER_S * 2)), 'next measure', queue_s=0.2, **_ON),
        ):
            pass
