"""A fresh ledger opened by many threads and processes at once: none may see ``database is locked``."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV
from lab_commons.box._store import _SCHEMA, connect

_N = 8
#: The tables the store's own schema declares -- read from it, so a new table never stales this test.
_TABLES = set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', _SCHEMA))


def _schema() -> set[str]:
    con = connect()
    try:
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r[0] for r in rows} - {'sqlite_sequence'}
    finally:
        con.close()


@pytest.mark.parametrize('attempt', range(3))
def test_a_fresh_ledger_opened_from_many_threads_at_once_never_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attempt: int
) -> None:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / f'box{attempt}.sqlite'))
    gate = threading.Barrier(_N)
    errors: list[BaseException] = []

    def one() -> None:
        gate.wait()
        try:
            connect().close()
        except BaseException as exc:  # noqa: BLE001 -- the assertion below names it
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(_N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _schema() == _TABLES


_CHILD = """
import sys, time
from lab_commons.box._store import _SCHEMA, connect
start = float(sys.argv[1])
while time.time() < start:
    pass
connect().close()
"""


def test_a_fresh_ledger_opened_from_many_processes_at_once_never_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    start = time.time() + 3.0
    procs = [
        subprocess.Popen(
            [sys.executable, '-c', _CHILD, str(start)], env=dict(os.environ), stderr=subprocess.PIPE, text=True
        )
        for _ in range(_N)
    ]
    failures = [err for p in procs if (err := p.communicate(timeout=60)[1]) and p.returncode != 0]
    assert failures == []
    assert _schema() == _TABLES
