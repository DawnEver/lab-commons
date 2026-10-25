"""A LENT resource: a named capacity its consumer declares (``Capacity.lent``), held on the one ledger."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import time
from multiprocessing.queues import Queue
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, Capacity, NotAdmitted, hold, holdings, take
from lab_commons.compute.model import Demand

_SLOTS = Capacity.lent({'jobslot:ws1': 2})


def _slot(name: str = 'jobslot:ws1') -> Demand:
    return Demand({name: 1, 'cpu': 0, 'mem_gb': 0}), 600


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    return path


def test_a_lent_name_is_namespaced_so_the_machine_file_cannot_declare_it_too() -> None:
    with pytest.raises(ValueError, match='jmag'):
        Capacity.lent({'jmag': 2})  # a machine-file name: a SECOND source of its count
    with pytest.raises(ValueError, match='jobslot:ws1'):
        Capacity.lent({'jobslot:ws1': 0})


@pytest.mark.usefixtures('box')
def test_a_seats_only_claim_never_samples_the_os() -> None:
    def refuse(_pids: object) -> object:
        msg = 'a seats-only decision read the OS'
        raise AssertionError(msg)

    with hold(*_slot(), 'w', 0, key='k', capacity=_SLOTS, read=refuse):  # type: ignore[arg-type]
        assert [h.seats for h in holdings()] == [{'jobslot:ws1': 1}]


def _racer(path: str, peak: Queue[int]) -> None:
    os.environ[BOX_ENV] = path
    with hold(*_slot(), f'racer-{os.getpid()}', 60, key='k', capacity=_SLOTS, poll_s=0.02):
        peak.put(sum(h.seats.get('jobslot:ws1', 0) for h in holdings()))
        time.sleep(0.1)


def test_processes_sharing_a_lent_capacity_never_exceed_it(box: Path) -> None:
    peak: Queue[int] = multiprocessing.Queue()
    racers = [multiprocessing.Process(target=_racer, args=(str(box), peak)) for _ in range(6)]
    for racer in racers:
        racer.start()
    seen = [peak.get(timeout=120) for _ in racers]
    for racer in racers:
        racer.join(120)
    assert all(racer.exitcode == 0 for racer in racers)
    assert max(seen) == 2  # full width reached, never exceeded


_TAKER = """
import sys, time
from lab_commons.box import Capacity, take
from lab_commons.compute.model import Demand
take(Demand({'jobslot:ws1': 2, 'cpu': 0, 'mem_gb': 0}), 600, 'taker', key='k',
     capacity=Capacity.lent({'jobslot:ws1': 2}))
print('held', flush=True)
time.sleep(600)
"""


@pytest.mark.usefixtures('box')
def test_a_killed_holder_frees_its_lent_slots() -> None:
    taker = subprocess.Popen([sys.executable, '-c', _TAKER], stdout=subprocess.PIPE, text=True, env=os.environ)
    try:
        assert taker.stdout is not None
        assert taker.stdout.readline().strip() == 'held'
        with pytest.raises(NotAdmitted):
            take(*_slot(), 'blocked', key='k', capacity=_SLOTS)  # PLANTED CONTROL: both slots are held
    finally:
        taker.kill()
        taker.wait(30)
    with hold(*_slot(), 'after', 1, key='k', capacity=_SLOTS):
        assert [h.what for h in holdings()] == ['after']
