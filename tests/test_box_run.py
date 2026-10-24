"""``box.run`` on every platform: the CLI refuses an undeclared limit, the scope wraps exactly the claim."""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from _box_fake import quiet

from lab_commons import box as box_package
from lab_commons.box import (
    BOX_ENV,
    CPU_ENV,
    Capacity,
    NotAdmitted,
    ProcessEnd,
    Refused,
    command,
    hold,
    holdings,
    read_ended,
    run,
    take,
    write_ended,
)
from lab_commons.box.__main__ import _parser, main
from lab_commons.box._linux import in_slurm_job, manager, scope_argv
from lab_commons.box.run import _ENFORCERS, BLAS_THREAD_VARS
from lab_commons.compute.model import Demand
from lab_commons.outcomes import Status


@pytest.mark.parametrize('missing', ['--cpu', '--mem-gb', '--wall', '--queue-s', '--key', '--outcome'])
def test_the_cli_refuses_a_missing_limit(missing: str, capsys: pytest.CaptureFixture[str]) -> None:
    given = {'--cpu': '1', '--mem-gb': '0.5', '--wall': '5', '--queue-s': '1', '--key': 'k', '--outcome': 'o.json'}
    args = [token for flag, value in given.items() if flag != missing for token in (flag, value)]
    with pytest.raises(SystemExit) as refused:
        main(['run', *args, '--', sys.executable, '-c', 'pass'])
    assert refused.value.code == 2
    assert missing in capsys.readouterr().err


def test_command_spells_every_declared_limit_so_the_cli_reads_back_the_same_demand() -> None:
    """The one spelling of a run through the box: parsed by the CLI, it is the demand and identity it was given."""
    demand = Demand({'jmag': 1, 'femm': 0, 'cpu': 3, 'mem_gb': 1.5}), 60
    argv = command(['tool', '--flag'], *demand, 7.0, key='k', what='w', outcome=Path('o.json'), fingerprint='f')
    assert argv[:3] == [sys.executable, '-m', 'lab_commons.box']
    ns = _parser().parse_args(argv[3:])
    assert ns.outcome == Path('o.json')
    read = Demand({**dict(ns.seat), 'cpu': ns.cpu, 'mem_gb': ns.mem_gb}), ns.wall
    assert (*read, ns.queue_s, ns.key, ns.what, ns.fingerprint) == (
        Demand({'jmag': 1, 'cpu': 3, 'mem_gb': 1.5}),
        60,
        7.0,
        'k',
        'w',
        'f',
    ), 'a zero seat is not spelled'
    assert ns.argv == ['--', 'tool', '--flag']


def test_the_cli_refuses_a_missing_command() -> None:
    with pytest.raises(SystemExit):
        main(['run', '--cpu', '1', '--mem-gb', '0.5', '--wall', '5', '--queue-s', '1', '--key', 'k', '--outcome', 'o'])


def test_the_record_reads_back_an_outcome_a_refusal_or_nothing(tmp_path: Path) -> None:
    """One reader: the ProcessEnd as written, a Refused naming why, and ``None`` for a box that left no record."""
    path = tmp_path / 'outcome.json'
    assert read_ended(path) is None, 'no record: the box crashed'
    killed = ProcessEnd(-9, 'memory', 7, 1.5, 2.0, 'job-object', 'k', 'f')
    write_ended(path, killed)
    assert read_ended(path) == killed
    assert killed.killed
    write_ended(path, Refused('cpu: wants 4 of 3'))
    refused = read_ended(path)
    assert isinstance(refused, Refused)
    assert str(refused) == 'cpu: wants 4 of 3'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['outcome.json'], 'written atomically, no staging left'


def test_the_cli_records_a_refusal_and_its_code_means_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(_ENFORCERS, sys.platform, None)  # no enforcer here: the box refuses before any child
    path = tmp_path / 'outcome.json'
    args = ['run', '--cpu', '1', '--mem-gb', '0.5', '--wall', '5', '--queue-s', '1', '--key', 'k']
    code = main([*args, '--outcome', str(path), '--', sys.executable, '-c', 'pass'])
    assert code != 0
    assert isinstance(read_ended(path), Refused)


def test_an_empty_argv_is_refused() -> None:
    with pytest.raises(ValueError, match='empty argv'):
        run([], Demand(cpu=1, mem_gb=0.5), 5, 'nothing', queue_s=1, key='k')


def test_the_scope_carries_exactly_the_claim() -> None:
    demand = Demand(cpu=2, mem_gb=0.5), 30
    argv = scope_argv('/usr/bin/systemd-run', manager(1000), ['solver', '-x'], demand[0])
    assert argv[:4] == ['/usr/bin/systemd-run', '--user', '--scope', '--quiet']
    assert {'MemoryMax=536870912', 'MemorySwapMax=0', 'CPUQuota=200%'} <= set(argv)
    assert argv[-3:] == ['--', 'solver', '-x']


def test_root_scopes_in_the_system_manager_and_a_user_in_its_delegated_one() -> None:
    uid = 1000
    root, user = manager(0), manager(uid)
    assert root.flags == ()
    assert root.controllers == Path('/sys/fs/cgroup/cgroup.controllers')
    assert user.flags == ('--user',)
    slice_ = f'/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service'
    assert user.controllers == Path(f'{slice_}/cgroup.controllers')
    argv = scope_argv('/usr/bin/systemd-run', root, ['git', 'fetch'], Demand(cpu=1, mem_gb=0.25))
    assert argv[:3] == ['/usr/bin/systemd-run', '--scope', '--quiet']


def test_only_the_jobs_own_cgroup_makes_a_slurm_run() -> None:
    """Measured on an Ada compute node (cgroup v1): ``freezer:/slurm/uid_<n>/job_<id>/step_0``."""
    inside = '10:freezer:/slurm/uid_80178097/job_7695270/step_0\n11:cpu,cpuacct:/user.slice\n'
    assert in_slurm_job('7695270', inside)
    assert not in_slurm_job('7695270', '11:cpu,cpuacct:/user.slice\n'), 'an inherited id on a login node'
    assert not in_slurm_job('769527', inside), 'another job id is not a prefix match'
    assert not in_slurm_job(None, inside), 'PLANTED CONTROL: no id, no job'


def test_a_platform_without_an_enforcer_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert 'darwin' not in _ENFORCERS
    with pytest.raises(Refused, match='no enforcer on darwin'):
        run([sys.executable, '-c', 'pass'], Demand(cpu=1, mem_gb=0.5), 5, 'mac', queue_s=1, key='k')


def test_the_key_and_fingerprint_ride_on_the_ledger_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    demand = Demand(cpu=1, mem_gb=0.5), 5
    with (
        hold(
            *demand,
            'tagged',
            1,
            capacity=(size := Capacity(cpu=2.0, mem_bytes=2**31, seats={})),
            read=quiet(size),
            key='case|fea',
            fingerprint='f0',
        ),  # fmt: skip
        sqlite3.connect(path) as con,
    ):
        assert con.execute('SELECT key, fingerprint FROM claims').fetchall() == [('case|fea', 'f0')]


def test_the_child_holds_against_the_capacity_its_caller_sized_the_demand_by(monkeypatch: pytest.MonkeyPatch) -> None:
    """ONE capacity reading: the caller's size reaches the child's hold, never re-read from the child's cwd.

    PLANTED CONTROL: without ``--capacity`` the child reads this box itself (``None``).
    """
    size = Capacity(cpu=12.0, mem_bytes=40 * 1024**3, seats={'jmag': 2}, kept_cpu=1.0, kept_bytes=2 * 1024**3)
    demand = Demand(cpu=2, mem_gb=4.0), 60
    argv = command(['tool'], *demand, 1.0, key='k', what='w', outcome=Path('o.json'), capacity=size)
    assert _parser().parse_args(argv[3:]).capacity == size
    held: list[Capacity | None] = []

    def fake_run(*_args: object, capacity: Capacity | None = None, **_kw: object) -> ProcessEnd:
        held.append(capacity)
        return ProcessEnd(0, 'exit', 0, 0.0, 0.0, 'fake', 'k', None)

    monkeypatch.setattr('lab_commons.box.__main__.run', fake_run)
    monkeypatch.setattr('lab_commons.box.__main__.write_ended', lambda *_a: None)
    assert main(argv[3:]) == 0
    unsized = command(['tool'], *demand, 1.0, key='k', what='w', outcome=Path('o.json'))
    assert main(unsized[3:]) == 0
    assert held == [size, None]


def test_the_child_is_told_the_cores_it_was_admitted_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Declared == used: the child's environment carries the claim's cores, so a pool inside sizes to them.

    PLANTED CONTROL: the caller's own environment is kept beside it, and the cores are the DEMAND's, not the box's.
    """
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    seen: list[Mapping[str, str] | None] = []

    def fake(_argv: object, _demand: object, _cwd: object, env: Mapping[str, str] | None, _ids: object) -> ProcessEnd:
        seen.append(env)
        return ProcessEnd(0, 'exit', 0, 0.0, 0.0, 'fake', 'k', None)

    monkeypatch.setitem(_ENFORCERS, sys.platform, fake)
    size = Capacity(cpu=12.0, mem_bytes=2**34, seats={})
    demand = Demand(cpu=3, mem_gb=0.5), 5
    run(['tool'], *demand, 'w', 1, key='k', env={'KEEP': '1'}, capacity=size, read=quiet(size))
    assert seen[0] is not None
    assert (seen[0][CPU_ENV], seen[0]['KEEP']) == ('3', '1')


def test_the_childs_thread_pools_are_sized_to_the_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every BLAS/OpenMP pool in the tree is sized to the claim's cores, never the box's.

    THE INCIDENT (lab-commons 57cafb9d5f, tests/base on a 32-thread Windows host): ``import numpy`` with no
    pool size COMMITS 756 MB (OpenBLAS reserves one buffer per box thread; 17 MB at one thread), and the
    Job Object limits commit, so a serial pytest sat at 880 MB before its first test and any child python
    importing numpy cost another 750 MB -- the part was killed past its 3 GB claim at 3 %.

    PLANTED CONTROL: a caller's own pool size is overridden; a box-wide figure inside a claim is the defect.
    """
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    seen: list[Mapping[str, str] | None] = []

    def fake(_argv: object, _demand: object, _cwd: object, env: Mapping[str, str] | None, _ids: object) -> ProcessEnd:
        seen.append(env)
        return ProcessEnd(0, 'exit', 0, 0.0, 0.0, 'fake', 'k', None)

    monkeypatch.setitem(_ENFORCERS, sys.platform, fake)
    size = Capacity(cpu=32.0, mem_bytes=2**34, seats={})
    demand = Demand(cpu=2, mem_gb=0.5), 5
    run(['tool'], *demand, 'w', 1, key='k', env={'OPENBLAS_NUM_THREADS': '32'}, capacity=size, read=quiet(size))
    assert seen[0] is not None
    assert {name: seen[0][name] for name in BLAS_THREAD_VARS} == dict.fromkeys(BLAS_THREAD_VARS, '2')


@pytest.mark.parametrize(
    ('reason', 'status'), [('memory', Status.OUT_OF_MEMORY), ('wall', Status.TIMEOUT), ('exit', None)]
)
def test_a_process_end_maps_its_kill_onto_the_one_status_vocabulary(reason: str, status: Status | None) -> None:
    """Plan A1.7: a box kill is a RETRYABLE status of lab_commons.outcomes; a normal exit carries none of its own."""
    ended = ProcessEnd(0 if reason == 'exit' else -9, reason, 0, 0.0, 0.0, 'fake', 'k', None)
    assert ended.status is status


def test_the_generic_name_is_gone_from_the_box() -> None:
    assert 'ProcessEnd' in box_package.__all__
    with pytest.raises(AttributeError):
        _ = importlib.import_module(box_package.__name__).Outcome


def test_a_run_handed_its_callers_claim_adopts_it_and_is_never_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ONE ADMISSION: the caller took the claim (:func:`take`), the run adopts it -- the box does not decide twice.

    PLANTED CONTROL: on the same full box a fresh admission IS refused, so the adopted run proves no second look.
    """
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    size = Capacity(cpu=2.0, mem_bytes=2 * 2**30, seats={})
    demand = Demand(cpu=1, mem_gb=1.5), 60
    taken = take(*demand, 'tests/x@sha', capacity=size, read=quiet(size), key='tests/x')
    with pytest.raises(NotAdmitted):
        take(*demand, 'tests/y@sha', capacity=size, read=quiet(size), key='tests/y')
    during: list[list[tuple[int, int]]] = []

    def fake(*_args: object) -> ProcessEnd:
        with sqlite3.connect(tmp_path / 'box.sqlite') as con:
            during.append(con.execute('SELECT id, pid FROM claims').fetchall())
        return ProcessEnd(0, 'exit', 0, 0.0, 0.0, 'fake', 'k', None)

    monkeypatch.setitem(_ENFORCERS, sys.platform, fake)
    ended = run(['tool'], *demand, 'w', 0, key='tests/x', claim=taken.id, capacity=size, read=quiet(size))
    assert ended.returncode == 0
    assert during == [[(taken.id, os.getpid())]], 'the one claim, held by the run, and no second row'
    assert holdings() == [], 'released when the run ends'
    with pytest.raises(Refused, match='not on this box'):
        run(['tool'], *demand, 'w', 0, key='tests/x', claim=taken.id, capacity=size, read=quiet(size))
    argv = command(['tool'], *demand, 0.0, key='k', what='w', outcome=Path('o.json'), claim=taken.id)
    assert _parser().parse_args(argv[3:]).claim == taken.id
