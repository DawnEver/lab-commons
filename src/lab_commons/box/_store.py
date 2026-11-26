"""The box ledger's FILE: where it lives, whether it may be trusted, and its schema.

Split from :mod:`lab_commons.box.ledger`, which keeps the decisions; see its docstring for why the file is
SQLite in WAL mode, machine-wide, and refused when another user could write it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from lab_commons.box.demand import Holding, Refused
from lab_commons.home import part

__all__ = [
    'BOX_ENV',
    'SCHEMA_VERSION',
    'LedgerVersionRefused',
    'box_path',
    'connect',
    'immediate',
    'migrate',
    'read',
    'untrusted',
]

#: Environment override of the ledger file (tests; a second box on one machine).
BOX_ENV: Final = 'LAB_COMMONS_BOX'


#: How long one transaction may wait for the write lock; a decision holds it for milliseconds.
_LOCK_S: Final = 30.0

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL,
    deadline REAL NOT NULL, started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT,
    kept INTEGER NOT NULL DEFAULT 0, probe TEXT,
    scope TEXT NOT NULL DEFAULT 'box', kind TEXT NOT NULL DEFAULT 'occupancy', window TEXT);
CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, name TEXT NOT NULL, amount REAL NOT NULL,
    at REAL NOT NULL, window_s REAL NOT NULL, what TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    gives_up REAL NOT NULL, what TEXT NOT NULL, cpu INTEGER, mem_bytes INTEGER, seats TEXT);
"""


def box_path() -> Path:
    """The ledger file: ``$LAB_COMMONS_BOX``, else the machine home's ``state/box.sqlite`` on every platform."""
    override = os.environ.get(BOX_ENV)
    if override:
        return Path(override).expanduser()
    return part('state') / 'box.sqlite'


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
    """Open the ledger, refusing an untrusted file and one at another :data:`SCHEMA_VERSION`."""
    path = box_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for one in (path.parent, path):
        _refuse_untrusted(one)
    con = sqlite3.connect(path, timeout=_LOCK_S, isolation_level=None)
    try:
        with _FIRST_OPEN:  # threads of one process queue here; other processes are met by the retry below
            _set_up(con)
    except BaseException:
        con.close()
        raise
    return con


#: Serialises this process's ledger set-up; a fresh file's WAL switch otherwise races itself.
_FIRST_OPEN: Final = threading.Lock()


def _set_up(con: sqlite3.Connection) -> None:
    """Switch *con*'s file to WAL, write the schema into a fresh file, and refuse one at another version.

    ``PRAGMA journal_mode=WAL`` on a fresh file needs an exclusive lock and SQLite does not run the busy
    handler for it, so a second opener sees ``database is locked`` at once: it is retried here, bounded by
    the same :data:`_LOCK_S` a transaction waits. The check runs in one ``BEGIN IMMEDIATE`` so two processes
    cannot both see a fresh file and both write its schema.
    """
    gives_up = time.monotonic() + _LOCK_S
    while True:
        try:
            con.execute('PRAGMA journal_mode=WAL')
            break
        except sqlite3.OperationalError as exc:
            if ('locked' not in str(exc) and 'busy' not in str(exc)) or time.monotonic() >= gives_up:
                raise
            time.sleep(0.01)
    with immediate(con):
        found = con.execute('PRAGMA user_version').fetchone()[0]
        if found == 0 and not con.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
            _write_current(con)
            return
        if found != SCHEMA_VERSION:
            raise LedgerVersionRefused(box_path(), found)


#: The ledger file's schema version, held in SQLite's ``PRAGMA user_version`` -- the file's own header, so the
#: version cannot disagree with the file it describes. 0 is every file written before the version existed.
#: Bump it with every :data:`_ADDED` row; :func:`migrate` moves a file forward, :func:`connect` never does.
SCHEMA_VERSION: Final = 1


class LedgerVersionRefused(Refused):
    """The ledger file is at a schema version this code does not write: older is migrated once, newer is refused.

    A NEWER file is refused, not read, though its columns only grow: this code would write rows that omit what
    the newer version requires of them. An install that predates :data:`SCHEMA_VERSION` checks nothing and reads
    a migrated file as before -- its own open finds every column present and adds none.
    """

    def __init__(self, path: Path, found: int) -> None:
        self.path, self.found = path, found
        if found < SCHEMA_VERSION:
            door = 'migrate it once: python -m lab_commons.box migrate'
        else:
            door = 'it was written by a newer lab-commons: run that version, or upgrade this install'
        super().__init__(
            f'box ledger {path} is at schema version {found}, this lab-commons writes {SCHEMA_VERSION}: refused '
            f'-- {door}'
        )


def _write_current(con: sqlite3.Connection) -> None:
    """Write the current schema into *con* (inside its transaction) and stamp :data:`SCHEMA_VERSION`."""
    for statement in filter(str.strip, _SCHEMA.split(';')):
        con.execute(statement)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


def migrate(path: Path | None = None) -> int:
    """Move the ledger at *path* (default :func:`box_path`) to :data:`SCHEMA_VERSION` ONCE; the version it was at.

    Adds every :data:`_ADDED` column the file lacks and stamps the version, in one ``BEGIN IMMEDIATE``. A file
    already current is left unchanged; one NEWER than this code is refused by :class:`LedgerVersionRefused`.
    """
    path = box_path() if path is None else path
    for one in (path.parent, path):
        _refuse_untrusted(one)
    con = sqlite3.connect(path, timeout=_LOCK_S, isolation_level=None)
    try:
        with immediate(con):
            found = con.execute('PRAGMA user_version').fetchone()[0]
            if found > SCHEMA_VERSION:
                raise LedgerVersionRefused(path, found)
            for statement in filter(str.strip, _SCHEMA.split(';')):
                con.execute(statement)
            for table, added in _ADDED.items():
                columns = {row[1] for row in con.execute(f'PRAGMA table_info({table})')}
                for name, spelled in added:
                    if name not in columns:
                        con.execute(f'ALTER TABLE {table} ADD COLUMN {spelled}')
            con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    finally:
        con.close()
    return found


#: Columns added after the file format was first written, per table: (name, its fixed definition).
#:
#: THE FILE IS SHARED BY EVERY lab-commons VERSION ON THE BOX, so its schema only ever GROWS: a new table, or a new
#: column that is nullable or has a default. Never a rename, a drop or a type change -- an older reader on the same
#: box would fail on its next open (2026-10-26: a ``seats`` -> ``amounts`` rename stopped every older process on
#: the box). ``test_box_store_is_additive`` refuses any other DDL. So a column keeps its first name: ``seats`` holds
#: EVERY amount but cores and memory, by name -- tool seats, lent slots, OS, consumables. Only :func:`migrate`
#: adds them to an existing file; a new row here bumps :data:`SCHEMA_VERSION` with it.
#: A ticket's demand is NULL on a ticket written before it was declared: such a ticket contends for everything.
#: A claim written before :mod:`lab_commons.resources` named scopes and kinds is the box's, OCCUPANCY, windowless.
#: A claim's ``devices`` (its device ids, JSON) is NULL when it holds none.
_ADDED: Final = {
    'claims': (
        ('kept', 'kept INTEGER NOT NULL DEFAULT 0'),
        ('probe', 'probe TEXT'),
        ('scope', "scope TEXT NOT NULL DEFAULT 'box'"),
        ('kind', "kind TEXT NOT NULL DEFAULT 'occupancy'"),
        ('window', 'window TEXT'),
        ('devices', 'devices TEXT'),
    ),
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
    """Every claim row of the BOX scope on *con*, oldest first, as :class:`~lab_commons.box.demand.Holding`.

    A row :mod:`lab_commons.resources` wrote for another scope (an API key, a cluster account) holds nothing here.
    """
    rows = con.execute(
        'SELECT what, pid, cpu, mem_bytes, seats, deadline, id, key, probe, kept, devices FROM claims '
        "WHERE scope = 'box' "
        'ORDER BY id'
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
            bool(kept),
            tuple(json.loads(devices or '[]')),
        )
        for what, pid, cpu, mem, seats, deadline, row_id, key, probe, kept, devices in rows
    ]
