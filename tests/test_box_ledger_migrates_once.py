"""FORMATS-MIGRATE-ONCE for the box ledger (ruling D62, 2026-11-18): the file carries ``PRAGMA user_version``.

Opening never alters a file. A fresh file gets the current schema and version; a file at an older version is
refused by name with the one-shot migration that moves it; a file NEWER than this code is refused too. Each
case is planted: the refusals below are the gate saying no, and the migrated file is the same gate saying yes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV
from lab_commons.box.__main__ import main
from lab_commons.box._store import _ADDED, SCHEMA_VERSION, LedgerVersionRefused, box_path, connect, migrate

#: The ledger as written before any :data:`_ADDED` column existed, and before the version did (user_version 0).
_FIRST_SHAPE = """
CREATE TABLE claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL,
    deadline REAL NOT NULL, started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT);
CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    gives_up REAL NOT NULL, what TEXT NOT NULL);
"""


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    return path


def _plant(path: Path, script: str, version: int) -> None:
    con = sqlite3.connect(path)
    con.executescript(script)
    con.execute(f'PRAGMA user_version = {version}')
    con.commit()
    con.close()


def _columns(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[1] for row in con.execute(f'PRAGMA table_info({table})')}
    finally:
        con.close()


def _version(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute('PRAGMA user_version').fetchone()[0]
    finally:
        con.close()


def test_a_fresh_ledger_is_written_at_the_current_version(ledger: Path) -> None:
    connect().close()
    assert _version(ledger) == SCHEMA_VERSION
    assert {name for name, _ in _ADDED['claims']} <= _columns(ledger, 'claims')


def test_an_older_ledger_is_refused_by_name_and_left_unaltered(ledger: Path) -> None:
    _plant(ledger, _FIRST_SHAPE, 0)
    with pytest.raises(LedgerVersionRefused) as refused:
        connect()
    text = str(refused.value)
    assert str(ledger) in text
    assert 'version 0' in text
    assert f'writes {SCHEMA_VERSION}' in text
    assert 'python -m lab_commons.box migrate' in text
    assert 'kept' not in _columns(ledger, 'claims'), 'an open altered the file'
    assert _version(ledger) == 0


def test_migrate_moves_an_older_ledger_once_and_it_then_opens(ledger: Path) -> None:
    _plant(ledger, _FIRST_SHAPE, 0)
    con = sqlite3.connect(ledger)
    con.execute("INSERT INTO claims VALUES (1, 7, 0, 1, 1, '{}', 9.0, 1.0, 'w', 'k', NULL)")
    con.commit()
    con.close()
    assert migrate() == 0
    assert _version(ledger) == SCHEMA_VERSION
    for table, added in _ADDED.items():
        assert {name for name, _ in added} <= _columns(ledger, table)
    con = connect()
    try:
        assert con.execute('SELECT scope, kind, kept FROM claims').fetchall() == [('box', 'occupancy', 0)]
    finally:
        con.close()
    assert migrate() == SCHEMA_VERSION, 'a second migration finds the file current and changes nothing'


def test_a_current_ledger_opens_unchanged(ledger: Path) -> None:
    connect().close()
    before = ledger.read_bytes()
    connect().close()
    assert _version(ledger) == SCHEMA_VERSION
    assert ledger.read_bytes() == before


def test_a_newer_ledger_is_refused_by_open_and_by_migrate(ledger: Path) -> None:
    connect().close()
    con = sqlite3.connect(ledger)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
    con.close()
    for door in (connect, migrate):
        with pytest.raises(LedgerVersionRefused, match=f'version {SCHEMA_VERSION + 1}.*newer lab-commons'):
            door()
    assert _version(ledger) == SCHEMA_VERSION + 1


def test_the_migrate_verb_migrates_this_boxs_ledger(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _plant(ledger, _FIRST_SHAPE, 0)
    assert main(['migrate']) == 0
    assert _version(box_path()) == SCHEMA_VERSION
    assert '(was 0)' in capsys.readouterr().out
