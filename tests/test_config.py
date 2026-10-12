"""``lab_commons.config`` -- the one per-machine file every package reads its own section of."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.config import CONFIG_ENV, SHARED_KEYS, USER_TABLE, User, config_path, machine, section, user


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
    _point(tmp_path, monkeypatch, '[hpc]\ncpus = 1\n[viz]\ndpi = 150\n')
    assert machine() == {'hpc': {'cpus': 1}, 'viz': {'dpi': 150}}
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


def test_the_user_table_has_one_default_per_shared_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(tmp_path, monkeypatch, None)
    assert user() == User()
    assert set(SHARED_KEYS) == {
        'text_editor_command',
        'log_level',
        'log_file_format',
        'log_console_format',
        'log_date_format',
    }


def test_the_user_table_is_read_and_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(tmp_path, monkeypatch, f'[{USER_TABLE}]\ntext_editor_command = "start"\nlog_level = "debug"\n')
    assert user().text_editor_command == 'start'
    assert user().log_level == 'DEBUG'


@pytest.mark.parametrize(
    ('text', 'match'),
    [
        ('[user]\neditor = "vim"\n', r"\[user\] has no key 'editor'"),
        ('[user]\nlog_level = "LOUD"\n', r'log_level must be one of'),
        ('[user]\nlog_date_format = 3\n', r'log_date_format must be a string'),
    ],
)
def test_a_bad_user_table_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, match: str) -> None:
    _point(tmp_path, monkeypatch, text)
    with pytest.raises((ValueError, TypeError), match=match):
        user()


@pytest.mark.parametrize(
    ('text', 'name', 'where'),
    [
        (
            '[motronics.utils]\ntext_editor_command = "start"\n',
            'motronics',
            r'\[motronics\.utils\]\.text_editor_command',
        ),
        ('[wdg_lab]\nlog_level = "INFO"\n', 'wdg_lab', r'\[wdg_lab\]\.log_level'),
        ('[[pkg.row]]\nlog_date_format = "%d"\n', 'pkg', r'\[pkg\.row\]\.log_date_format'),
    ],
)
def test_a_package_table_repeating_a_shared_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, name: str, where: str
) -> None:
    _point(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match=where + r'.*move it to \[user\].*delete it here'):
        section(name)


def test_a_package_table_without_shared_keys_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PLANTED CONTROL: the refusal is about a SHARED key, not about any key a package owns."""
    _point(tmp_path, monkeypatch, '[motronics.utils]\nlog_level_note = "x"\n[user]\nlog_level = "INFO"\n')
    assert section('motronics') == {'utils': {'log_level_note': 'x'}}
    assert section('user') == {'log_level': 'INFO'}
