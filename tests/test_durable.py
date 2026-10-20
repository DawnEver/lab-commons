"""``lab_commons.durable`` -- every state file is replaced whole or not at all, and a torn one never kills a run.

INCIDENT 2026-10-20: the box's Python was deleted at 02:02 mid-run; ``compute-learned/<repo>.json``, last
written at 02:01:08 by a bare ``write_text``, was left as 5746 bytes of NUL, and every ``learned()`` call
raised ``JSONDecodeError`` -- every run on the box died at admission until a person found the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab_commons import durable
from lab_commons.compute import learned as learned_module


def test_a_write_interrupted_between_temp_and_replace_leaves_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED: the rename never happens; the reader still sees the whole previous file, and no temp is left."""
    target = tmp_path / 'table.json'
    durable.durable_write(target, b'{"old": 1}')

    def dies(_self: Path, _target: Path) -> None:
        raise KeyboardInterrupt  # the process is killed between the staged write and the rename

    monkeypatch.setattr(Path, 'replace', dies)
    with pytest.raises(KeyboardInterrupt):
        durable.durable_write(target, b'{"new": 2}')
    monkeypatch.undo()
    assert json.loads(target.read_text(encoding='utf-8')) == {'old': 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ['table.json'], 'a torn temp was left behind'


def test_a_completed_write_replaces_the_file(tmp_path: Path) -> None:
    target = tmp_path / 'deep' / 'table.json'
    durable.durable_write(target, b'{"a": 1}')
    durable.durable_write(target, b'{"a": 2}')
    assert durable.read_table(target) == {'a': 2}


def test_a_nul_filled_table_is_quarantined_warned_about_and_read_as_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PLANTED: the measured 5746 NUL bytes."""
    target = tmp_path / 'ef002c4ad573d799.json'
    target.write_bytes(b'\0' * 5746)
    assert durable.read_table(target) == {}
    assert not target.exists()
    (moved,) = tmp_path.glob('ef002c4ad573d799.json.corrupt-*')
    assert moved.read_bytes() == b'\0' * 5746, 'the evidence is kept, not deleted'
    err = capsys.readouterr().err
    assert 'UNREADABLE' in err
    assert str(target) in err


def test_an_absent_table_is_empty_and_quiet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert durable.read_table(tmp_path / 'none.json') == {}
    assert capsys.readouterr().err == ''


def test_learned_survives_a_torn_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the incident's own reader: a NUL table no longer raises at admission."""
    monkeypatch.setattr(learned_module, 'directory', lambda: tmp_path)
    learned_module._path('repo').write_bytes(b'\0' * 64)
    assert learned_module.learned('repo') == {}
    learned_module._write('repo', {'runs': []})
    assert learned_module.learned('repo') == {'runs': [], 'repo': 'repo'}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith('.tmp')]
