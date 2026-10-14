"""``box.run`` on every platform: the CLI refuses an undeclared limit, the scope wraps exactly the claim."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, Capacity, Demand, Refused, admit, run
from lab_commons.box.__main__ import main
from lab_commons.box._linux import scope_argv
from lab_commons.box.run import _ENFORCERS


@pytest.mark.parametrize('missing', ['--cpu', '--mem-gb', '--wall', '--queue-s', '--key'])
def test_the_cli_refuses_a_missing_limit(missing: str, capsys: pytest.CaptureFixture[str]) -> None:
    given = {'--cpu': '1', '--mem-gb': '0.5', '--wall': '5', '--queue-s': '1', '--key': 'k'}
    args = [token for flag, value in given.items() if flag != missing for token in (flag, value)]
    with pytest.raises(SystemExit) as refused:
        main(['run', *args, '--', sys.executable, '-c', 'pass'])
    assert refused.value.code == 2
    assert missing in capsys.readouterr().err


def test_the_cli_refuses_a_missing_command() -> None:
    with pytest.raises(SystemExit):
        main(['run', '--cpu', '1', '--mem-gb', '0.5', '--wall', '5', '--queue-s', '1', '--key', 'k', '--'])


def test_an_empty_argv_is_refused() -> None:
    with pytest.raises(ValueError, match='empty argv'):
        run([], Demand(cpu=1, mem_gb=0.5, wall_s=5, seats={}), 'nothing', queue_s=1, key='k')


def test_the_scope_carries_exactly_the_claim() -> None:
    argv = scope_argv('/usr/bin/systemd-run', ['solver', '-x'], Demand(cpu=2, mem_gb=0.5, wall_s=30, seats={}))
    assert argv[:4] == ['/usr/bin/systemd-run', '--user', '--scope', '--quiet']
    assert {'MemoryMax=536870912', 'MemorySwapMax=0', 'CPUQuota=200%', 'RuntimeMaxSec=30'} <= set(argv)
    assert argv[-3:] == ['--', 'solver', '-x']


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
