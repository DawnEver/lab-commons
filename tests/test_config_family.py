"""``lab_commons.config`` family layer: a shared file merged UNDER the machine file, read from a local path."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lab_commons import config
from lab_commons.config import CONFIG_ENV, section


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    config._stale_warned.clear()


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine: str, family: str | None, head: str = '') -> Path:
    checkout = tmp_path / 'checkout'
    checkout.mkdir(exist_ok=True)
    if family is not None:
        (checkout / 'family.toml').write_text(family, encoding='utf-8')
    declared = f'[family]\nsource = {checkout.as_posix()!r}\n{head}' + machine
    path = tmp_path / 'machine.toml'
    path.write_text(declared, encoding='utf-8')
    monkeypatch.setenv(CONFIG_ENV, str(path))
    return checkout / 'family.toml'


def test_no_family_declared_reads_the_machine_file_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'machine.toml'
    path.write_text('[hpc]\ncpus = 1\n', encoding='utf-8')
    monkeypatch.setenv(CONFIG_ENV, str(path))
    assert section('hpc') == {'cpus': 1}


def test_the_family_table_merges_under_the_machine_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        tmp_path,
        monkeypatch,
        '[ai_lab]\nlocal = 1\n[ai_lab.spend_cap]\nextra = 2\n',
        '[ai_lab.spend_cap]\nusd = 50\n[ai_lab.standing]\nmonth = "2026-11"\n',
    )
    assert section('ai_lab') == {'local': 1, 'spend_cap': {'usd': 50, 'extra': 2}, 'standing': {'month': '2026-11'}}
    assert section('family_only_absent') == {}


def test_arrays_of_tables_come_whole_from_the_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, '', '[[hpc.grant]]\nuser = "a"\n[[hpc.grant]]\nuser = "b"\n')
    assert section('hpc') == {'grant': [{'user': 'a'}, {'user': 'b'}]}


def test_a_restated_key_refuses_and_a_local_only_key_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, '[hpc]\nmargin = 2\n', '[hpc]\nmargin = 1\n')
    with pytest.raises(ValueError, match=r'\[hpc\]\.margin.*delete it.*family'):
        section('hpc')
    _setup(tmp_path, monkeypatch, '[hpc]\nmargin = 2\n', '[hpc]\nother = 1\n')
    assert section('hpc') == {'margin': 2, 'other': 1}


def test_a_restated_array_of_tables_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, '[[hpc.grant]]\nuser = "x"\n', '[[hpc.grant]]\nuser = "a"\n')
    with pytest.raises(ValueError, match=r'\[hpc\]\.grant'):
        section('hpc')


@pytest.mark.parametrize('table', ['repos', 'writeroots', 'sync', 'family'])
def test_a_local_only_table_in_the_family_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str) -> None:
    _setup(tmp_path, monkeypatch, '', f'[{table}]\nx = 1\n[hpc]\ncpus = 1\n')
    with pytest.raises(ValueError, match=rf'\[{table}\].*local'):
        section('hpc')


def test_an_absent_declared_family_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, '[hpc]\ncpus = 1\n', None)
    with pytest.raises(FileNotFoundError, match=r'family\.toml.*\[family\]'):
        section('hpc')


def test_file_names_a_path_inside_the_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / 'repo' / 'sub').mkdir(parents=True)
    (tmp_path / 'repo' / 'sub' / 'f.toml').write_text('[hpc]\ncpus = 3\n', encoding='utf-8')
    path = tmp_path / 'm.toml'
    path.write_text(f'[family]\nsource = {(tmp_path / "repo").as_posix()!r}\nfile = "sub/f.toml"\n', encoding='utf-8')
    monkeypatch.setenv(CONFIG_ENV, str(path))
    assert section('hpc') == {'cpus': 3}


def test_a_stale_family_warns_once_and_still_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    family = _setup(tmp_path, monkeypatch, '', '[hpc]\ncpus = 1\n', head='max_age = "1 h"\n')
    assert section('hpc') == {'cpus': 1}
    assert capsys.readouterr().err == ''  # planted control: fresh is silent
    old = time.time() - 7200
    os.utime(family, (old, old))
    assert section('hpc') == {'cpus': 1}
    assert section('hpc') == {'cpus': 1}
    err = capsys.readouterr().err
    assert err.count('warning: family config') == 1
    assert str(family) in err


def test_a_bad_max_age_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, '', '[hpc]\n', head='max_age = "soon"\n')
    with pytest.raises(ValueError, match='max_age'):
        section('hpc')
