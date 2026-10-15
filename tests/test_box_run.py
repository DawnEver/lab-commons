"""``box.run`` on every platform: the CLI refuses an undeclared limit, the scope wraps exactly the claim."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, Capacity, Demand, Outcome, Refused, admit, command, read_ended, run, write_ended
from lab_commons.box.__main__ import _parser, main
from lab_commons.box._linux import in_slurm_job, manager, scope_argv
from lab_commons.box.run import _ENFORCERS


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
    demand = Demand(cpu=3, mem_gb=1.5, wall_s=60, seats={'jmag': 1, 'femm': 0})
    argv = command(['tool', '--flag'], demand, 7.0, key='k', what='w', outcome=Path('o.json'), fingerprint='f')
    assert argv[:3] == [sys.executable, '-m', 'lab_commons.box']
    ns = _parser().parse_args(argv[3:])
    assert ns.outcome == Path('o.json')
    read = Demand(cpu=ns.cpu, mem_gb=ns.mem_gb, wall_s=ns.wall, seats=dict(ns.seat))
    assert (read, ns.queue_s, ns.key, ns.what, ns.fingerprint) == (
        Demand(3, 1.5, 60, {'jmag': 1}),
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
    """One reader: the Outcome as written, a Refused naming why, and ``None`` for a box that left no record."""
    path = tmp_path / 'outcome.json'
    assert read_ended(path) is None, 'no record: the box crashed'
    killed = Outcome(-9, 'memory', 7, 1.5, 2.0, 'job-object', 'k', 'f')
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
        run([], Demand(cpu=1, mem_gb=0.5, wall_s=5, seats={}), 'nothing', queue_s=1, key='k')


def test_the_scope_carries_exactly_the_claim() -> None:
    demand = Demand(cpu=2, mem_gb=0.5, wall_s=30, seats={})
    argv = scope_argv('/usr/bin/systemd-run', manager(1000), ['solver', '-x'], demand)
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
    argv = scope_argv('/usr/bin/systemd-run', root, ['git', 'fetch'], Demand(cpu=1, mem_gb=0.25, wall_s=60, seats={}))
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
        run([sys.executable, '-c', 'pass'], Demand(cpu=1, mem_gb=0.5, wall_s=5, seats={}), 'mac', queue_s=1, key='k')


def test_the_key_and_fingerprint_ride_on_the_ledger_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    demand = Demand(cpu=1, mem_gb=0.5, wall_s=5, seats={})
    with (
        admit(
            demand, 'tagged', 1, capacity=Capacity(cpu=2.0, mem_bytes=2**31, seats={}), key='case|fea', fingerprint='f0'
        ),
        sqlite3.connect(path) as con,
    ):
        assert con.execute('SELECT key, fingerprint FROM claims').fetchall() == [('case|fea', 'f0')]
