"""The box ledger's FILE: where it lives, whether it may be trusted, and its schema.

Split from :mod:`lab_commons.box.ledger`, which keeps the decisions; see its docstring for why the file is
SQLite in WAL mode, machine-wide, and refused when another user could write it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from lab_commons.box.demand import Holding, Refused

__all__ = ['BOX_ENV', 'box_path', 'connect', 'immediate', 'read', 'untrusted']

#: Environment override of the ledger file (tests; a second box on one machine).
BOX_ENV: Final = 'LAB_COMMONS_BOX'


#: How long one transaction may wait for the write lock; a decision holds it for milliseconds.
_LOCK_S: Final = 30.0

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL,
    deadline REAL NOT NULL, started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT,
    kept INTEGER NOT NULL DEFAULT 0, probe TEXT);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    gives_up REAL NOT NULL, what TEXT NOT NULL, cpu INTEGER, mem_bytes INTEGER, seats TEXT);
"""


def box_path() -> Path:
    """The ledger file; see the module docstring for why it lives there."""
    override = os.environ.get(BOX_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == 'nt':
        return Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'lab-commons' / 'box.sqlite'
    state = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(state) / 'lab-commons' / 'box.sqlite'


def untrusted(*, mode: int, uid: int, me: int, link: bool) -> str | None:
    """Why a ledger path with POSIX *mode*, owner *uid* and *link*-ness is untrusted by user *me*, else None."""
    if link:
        return 'is a symlink'
    if uid != me:
        return f'is owned by uid {uid}, not this user ({me})'
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f'is writable by others (mode {stat.S_IMODE(mode):o})'
    return None


def _refuse_untrusted(path: Path) -> None:
    """Raise :class:`Refused` when *path* (the ledger or its directory) could be written by another user."""
    if not path.exists() and not path.is_symlink():
        return
    st = path.lstat()
    link = stat.S_ISLNK(st.st_mode)
    verdict = (
        untrusted(mode=st.st_mode, uid=st.st_uid, me=os.geteuid(), link=link)
        if os.name != 'nt'
        else ('is a symlink' if link else None)
    )
    if verdict is not None:
        msg = f'box ledger {path} {verdict}: refused -- its rows pin seats and name probes the reaper runs'
        raise Refused(msg)


def connect() -> sqlite3.Connection:
    """Open the ledger, refusing an untrusted file, with the schema moved forward to this version."""
    path = box_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for one in (path.parent, path):
        _refuse_untrusted(one)
    con = sqlite3.connect(path, timeout=_LOCK_S, isolation_level=None)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(_SCHEMA)
    for table, added in _ADDED.items():
        columns = {row[1] for row in con.execute(f'PRAGMA table_info({table})')}
        for name, spelled in added:
            if name not in columns:  # a ledger file written before the column existed: the schema moves forward
                con.execute(f'ALTER TABLE {table} ADD COLUMN {spelled}')
    return con


#: Columns added after the file format was first written, per table: (name, its fixed definition).
#: A ticket's demand is NULL on a ticket written before it was declared: such a ticket contends for everything.
_ADDED: Final = {
    'claims': (('kept', 'kept INTEGER NOT NULL DEFAULT 0'), ('probe', 'probe TEXT')),
    'tickets': (('cpu', 'cpu INTEGER'), ('mem_bytes', 'mem_bytes INTEGER'), ('seats', 'seats TEXT')),
}


@contextmanager
def immediate(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One ``BEGIN IMMEDIATE`` transaction: committed on exit, rolled back on any exception."""
    con.execute('BEGIN IMMEDIATE')
    try:
        yield con
    except BaseException:
        con.execute('ROLLBACK')
        raise
    con.execute('COMMIT')


def read(con: sqlite3.Connection) -> list[Holding]:
    """Every claim row on *con*, oldest first, as :class:`~lab_commons.box.demand.Holding`."""
    rows = con.execute(
        'SELECT what, pid, cpu, mem_bytes, seats, deadline, id, key, probe FROM claims ORDER BY id'
    ).fetchall()
    return [
        Holding(
            what,
            pid,
            cpu,
            mem,
            json.loads(seats),
            deadline,
            row_id,
            key,
            None if probe is None else tuple(json.loads(probe)),
        )
        for what, pid, cpu, mem, seats, deadline, row_id, key, probe in rows
    ]
