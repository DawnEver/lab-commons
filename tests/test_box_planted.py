"""A row this code could not have written pins nothing: the planted-claim attack (motronics SR-20261016-031).

The attack: a local writer plants a claim whose time lies in the future -- a start not yet reached, or a
deadline past any wall a :class:`Demand` may declare -- beside a LIVE pid, so neither pid liveness nor the
deadline ever reaps it and every seat it names is pinned. The ledger refuses such a row on sight.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from _box_fake import quiet

from lab_commons.box import BOX_ENV, WALL_CEILING_S, Capacity, Demand, Refused, hold, holdings
from lab_commons.box.ledger import untrusted
from lab_commons.liveness import creation_stamp

_CAP = Capacity(cpu=4.0, mem_bytes=8 * 2**30, seats={'jmag': 1})
_ON: dict[str, Any] = {'key': 'k', 'capacity': _CAP, 'read': quiet(_CAP)}
_SEAT = Demand(cpu=0, mem_gb=0, wall_s=60, seats={'jmag': 1})


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    with hold(Demand(1, 1.0, 60, {}), 'schema', queue_s=1, **_ON):
        pass
    return path


def _plant(box: Path, *, started: float, deadline: float) -> None:
    """A LIVE holder (this very process, its true stamp) persisted with an alive probe: only time can reap it."""
    pid = os.getpid()
    with sqlite3.connect(box) as con:
        con.execute(
            'INSERT INTO claims (pid, created, cpu, mem_bytes, seats, deadline, started, what, key, kept, probe) '
            'VALUES (?,?,0,0,?,?,?,?,?,1,?)',
            (pid, creation_stamp(pid), json.dumps({'jmag': 1}), deadline, started, 'planted', 'planted',
             json.dumps([sys.executable, '-c', 'pass'])),
        )  # fmt: skip


@pytest.mark.parametrize(
    ('started', 'deadline'),
    [(3600.0, 3660.0), (0.0, 10 * 365 * 86400.0)],
    ids=['future-start', 'deadline-past-any-wall'],
)
def test_a_planted_claim_with_a_future_time_cannot_pin_a_seat(box: Path, started: float, deadline: float) -> None:
    now = time.time()
    _plant(box, started=now + started, deadline=now + deadline)
    with hold(_SEAT, 'real solve', queue_s=1, **_ON):
        assert [row.what for row in holdings()] == ['real solve'], 'the planted row was reaped on sight'


def test_planted_control_an_honest_live_claim_still_holds_the_seat(box: Path) -> None:
    now = time.time()
    _plant(box, started=now, deadline=now + 600)
    with pytest.raises(Refused), hold(_SEAT, 'second', queue_s=0.2, **_ON):
        pass


def test_a_planted_ticket_with_a_future_give_up_does_not_block_the_queue(box: Path) -> None:
    pid = os.getpid()
    with sqlite3.connect(box) as con:
        con.execute(
            'INSERT INTO tickets (pid, created, gives_up, what, cpu, mem_bytes, seats) VALUES (?,?,?,?,?,?,?)',
            (pid, creation_stamp(pid), time.time() + 10 * WALL_CEILING_S, 'planted', None, None, None),
        )
    with hold(_SEAT, 'real solve', queue_s=1, **_ON):
        pass


def test_a_wall_or_a_wait_past_the_ceiling_is_refused() -> None:
    with pytest.raises(ValueError, match='wall_s'):
        Demand(cpu=1, mem_gb=1.0, wall_s=WALL_CEILING_S + 1, seats={})
    with pytest.raises(ValueError, match='queue_s'), hold(_SEAT, 'w', queue_s=WALL_CEILING_S + 1, **_ON):
        pass


@pytest.mark.parametrize(
    ('mode', 'uid', 'kind', 'refused'),
    [
        (stat.S_IFREG | 0o600, 1000, 'file', None),
        (stat.S_IFREG | 0o666, 1000, 'file', 'writable by others'),
        (stat.S_IFREG | 0o620, 1000, 'file', 'writable by others'),
        (stat.S_IFREG | 0o600, 0, 'file', 'owned by uid 0'),
        (stat.S_IFREG | 0o600, 1000, 'link', 'symlink'),
    ],
    ids=['own', 'world-writable', 'group-writable', 'foreign-owner', 'symlink'],
)
def test_a_ledger_another_user_could_write_is_untrusted(
    mode: int,
    uid: int,
    kind: str,
    refused: str | None,
) -> None:
    verdict = untrusted(mode=mode, uid=uid, me=1000, link=kind == 'link')
    assert (verdict is None) if refused is None else (refused in (verdict or ''))
