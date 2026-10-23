"""A seated preparation/read stage reserves memory without entering a solver CPU queue."""

import json
import os
import sqlite3
import time

import pytest
from _box_fake import quiet

from lab_commons.box import BOX_ENV, Capacity, Demand, NotAdmitted, hold, holdings
from lab_commons.box.run import run


def test_a_seated_stage_reserves_memory_and_no_solver_cpu(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    capacity = Capacity(cpu=4, mem_bytes=8 * 2**30, seats={'designer': 1})
    kwargs = {'capacity': capacity, 'read': quiet(capacity), 'outside': True}
    stage = Demand(cpu=0, mem_gb=2, wall_s=60, seats={'designer': 1})
    with hold(Demand(cpu=4, mem_gb=1, wall_s=600, seats={}), 'solver', 0, key='solver', **kwargs):
        with hold(stage, 'prepare', 0, key='prepare', **kwargs):
            rows = {row.what: row for row in holdings()}
            assert rows['prepare'].cpu == 0
            assert rows['prepare'].mem_bytes == 2 * 2**30
            with pytest.raises(NotAdmitted, match='designer'), hold(stage, 'read', 0, key='read', **kwargs):
                pytest.fail('A second stage exceeded the seat capacity')
        with (
            pytest.raises(NotAdmitted, match='mem_gb'),
            hold(Demand(cpu=0, mem_gb=8, wall_s=60, seats={'designer': 1}), 'large', 0, key='large', **kwargs),
        ):
            pytest.fail('A stage bypassed memory admission')
    assert holdings() == []


def test_a_stage_bypasses_a_cpu_waiter_without_delaying_its_reservation(tmp_path, monkeypatch) -> None:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    capacity = Capacity(cpu=4, mem_bytes=8 * 2**30, seats={'designer': 1})
    kwargs = {'capacity': capacity, 'read': quiet(capacity), 'outside': True}
    with hold(Demand(cpu=4, mem_gb=1, wall_s=600, seats={}), 'solver', 0, key='solver', **kwargs):
        with sqlite3.connect(path) as con:
            con.execute(
                'INSERT INTO tickets (pid, created, gives_up, what, cpu, mem_bytes, seats) VALUES (?,NULL,?,?,?,?,?)',
                (os.getpid(), time.time() + 600, 'next solver', 4, 2**30, json.dumps({})),
            )
        with hold(Demand(cpu=0, mem_gb=2, wall_s=60, seats={'designer': 1}), 'read', 0, key='read', **kwargs):
            assert {row.what for row in holdings()} == {'solver', 'read'}


@pytest.mark.parametrize('cpu', [False, -1, 0.0])
def test_a_seat_never_makes_an_invalid_cpu_count_valid(cpu) -> None:
    with pytest.raises(ValueError, match='cpu'):
        Demand(cpu=cpu, mem_gb=2, wall_s=60, seats={'designer': 1})


def test_zero_cpu_still_cannot_be_used_as_a_process_execution_limit() -> None:
    stage = Demand(cpu=0, mem_gb=2, wall_s=60, seats={'designer': 1})
    with pytest.raises(ValueError, match='positive cores'):
        run(['not-launched'], stage, 'stage', 0, key='stage')


def test_memory_can_be_claimed_separately_from_a_seat() -> None:
    assert Demand(cpu=0, mem_gb=2, wall_s=60, seats={}).mem_bytes == 2 * 2**30
    with pytest.raises(ValueError, match='mem_gb'):
        Demand(cpu=0, mem_gb=0, wall_s=60, seats={})
