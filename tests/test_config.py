"""``lab_commons.config`` -- the one per-machine file every package reads its own section of."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.config import CONFIG_ENV, config_path, machine, section


def _point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None) -> Path:
    path = tmp_path / 'config.toml'
    if text is not None:
        path.write_text(text, encoding='utf-8')
    monkeypatch.setenv(CONFIG_ENV, str(path))
    return path


def test_the_default_path_and_the_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    assert config_path() == Path.home() / '.config' / 'lab-commons' / 'config.toml'
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / 'c.toml'))
    assert config_path() == tmp_path / 'c.toml'


def test_a_missing_file_is_an_empty_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(tmp_path, monkeypatch, None)
    assert machine() == {}
    assert section('hpc') == {}


def test_a_section_is_its_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(tmp_path, monkeypatch, '[hpc]\nworkstation = "w"\n[viz]\ndpi = 150\n')
    assert machine() == {'hpc': {'workstation': 'w'}, 'viz': {'dpi': 150}}
    assert section('viz') == {'dpi': 150}
    assert section('absent') == {}


def test_a_section_that_is_not_a_table_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(tmp_path, monkeypatch, 'hpc = 3\n')
    with pytest.raises(TypeError, match=r'\[hpc\] must be a table'):
        section('hpc')


def test_the_legacy_hpc_file_is_refused_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _point(tmp_path, monkeypatch, '')
    (path.parent / 'hpc.toml').write_text('workstation = "w"\n', encoding='utf-8')
    with pytest.raises(ValueError, match=r'hpc\.toml.*move it under \[hpc\] in config\.toml'):
        machine()
