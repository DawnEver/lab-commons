"""AN EXTERNAL JOB ON THIS BOX IS A CLAIM OF ITS REAL LOAD (:func:`lab_commons.box.external`).

A JMAG Job System job runs on this node under the vendor's scheduler: no process of ours runs it, yet it takes
cores, memory, a JMAG seat and one of the scheduler's slots. Held as an external claim -- admitted by the engine's
rule, persisted with its liveness probe -- every later admission sees that load as held.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from _box_fake import quiet

from lab_commons.box import BOX_ENV, GB, Capacity, DuplicateKey, NotAdmitted, external, holdings, release
from lab_commons.box.ledger import reap
from lab_commons.box.providers import box_engine

CAPACITY = Capacity(cpu=8.0, mem_bytes=16 * GB, seats={'jmag': 4})
_QUIET = quiet(CAPACITY)
_ALIVE = [sys.executable, '-c', 'pass']
_DEAD = [sys.executable, '-c', 'import sys; sys.exit(1)']
_JOB = {'cpu': 4, 'mem_gb': 2, 'jmag': 1, 'jobslot:ws1': 1}


def _external(key: str, probe: list[str] = _ALIVE, **kw: object) -> object:
    return external(
        _JOB,
        600,
        f'jmag job {key}',
        key=key,
        probe=probe,
        lent={'jobslot:ws1': 2},
        capacity=CAPACITY,
        read=_QUIET,
        **kw,
    )


def test_an_external_job_is_held_at_its_load_past_the_call_and_released_on_evidence() -> None:
    claim = _external('job-1')
    (row,) = holdings()
    assert (row.cpu, row.mem_bytes, dict(row.seats)) == (4, 2 * GB, {'jmag': 1, 'jobslot:ws1': 1})
    assert (row.key, row.probe, row.kept) == ('job-1', tuple(_ALIVE), True)
    free = box_engine(capacity=CAPACITY, read=_QUIET, lent={'jobslot:ws1': 2}).free('box')
    assert (free['cpu'], free['jmag'], free['jobslot:ws1']) == (4.0, 3.0, 1.0), 'every later admission sees it held'
    release(claim)
    assert holdings() == [], 'PLANTED CONTROL: released on terminal evidence, it holds nothing'


def test_the_lent_slots_bound_how_many_jobs_run_here() -> None:
    _external('job-1')
    _external('job-2')
    with pytest.raises(NotAdmitted, match='jobslot:ws1'):
        _external('job-3')


def test_a_job_is_held_once_under_its_key() -> None:
    _external('job-1')
    with pytest.raises(DuplicateKey):
        _external('job-1')


def test_an_external_job_needs_a_liveness_probe() -> None:
    with pytest.raises(ValueError, match='liveness probe'):
        _external('job-1', probe=[])


def _from_another_process(key: str, probe: list[str]) -> None:
    """A client holds the job externally in ANOTHER process and exits, as a solve's launcher does."""
    code = (
        'from lab_commons.box import GB, Capacity, external; from lab_commons.box.supply import Reading; '
        f'c = Capacity(cpu=8.0, mem_bytes=16 * GB, seats={{"jmag": 4}}); r = Reading(8.0, 8.0, 16 * GB, 16 * GB); '
        f'external({_JOB!r}, 600, "job", key={key!r}, probe={probe!r}, lent={{"jobslot:ws1": 2}}, capacity=c, '
        'read=lambda _p: r)'
    )
    subprocess.run(
        [sys.executable, '-c', code], check=True, timeout=120, env={**os.environ, BOX_ENV: os.environ[BOX_ENV]}
    )


def test_a_job_its_probe_finds_gone_is_reaped_once_its_client_left() -> None:
    _from_another_process('gone', _DEAD)
    _from_another_process('running', _ALIVE)
    reap()
    assert [row.key for row in holdings()] == ['running'], 'PLANTED CONTROL: a live job outlives its client'
