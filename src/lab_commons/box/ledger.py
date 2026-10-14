"""The ONE ledger of running claims on this machine, and the bounded admission into it.

A CLAIM EXISTS ONLY FOR A RUNNING PROCESS. It is written when the launcher is admitted, deleted
when it leaves, and equals the run's declared :class:`~lab_commons.box.demand.Demand` -- which
phase 2 makes the kernel limit of that process. A claim therefore cannot be idle-but-held: what
it reserves is exactly what its process is allowed to use, and it ends no later than its
deadline. A row whose launcher is gone (pid absent, or its creation stamp differs: a recycled
pid) or whose deadline passed by more than :data:`GRACE_S` is reaped by the next admission, so
no crash leaves memory booked.

A WAITER HOLDS NOTHING. It takes a FIFO ticket that orders it in the queue and counts for
nothing in :func:`~lab_commons.box.demand.fits`. Queueing is STRICT FIFO: only the oldest live
ticket may be admitted, so a large demand is never starved by a stream of small ones. The price
is head-of-line blocking, and it is bounded on both sides, which is why EVERY WAIT ENDS: the head
waits at most until the holders' deadlines (each claim ends by then), and every waiter gives up
at its own ``queue_s`` -- a required argument -- and its ticket is reaped with it.

The file is SQLite in WAL mode, every decision inside ``BEGIN IMMEDIATE``: one writer at a time
across every process on the box, so two racing admissions cannot both see the same free room.
Location (:func:`box_path`): ``$LAB_COMMONS_BOX`` if set, else on Windows
``%ProgramData%/lab-commons/box.sqlite`` -- machine-wide, because the box is shared by every
session of its user (agents, services, terminals) and one manager per MACHINE is the point;
ProgramData admits a user-created subdirectory without elevation. Elsewhere
``$XDG_STATE_HOME/lab-commons/box.sqlite`` (``~/.local/state`` by default): per-user, since a
POSIX machine-wide state dir needs root to create; on a single-user box the two coincide.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lab_commons.box.demand import Capacity, Demand, Holding, Refused, fits
from lab_commons.liveness import creation_stamp, still_the_same_process

__all__ = ['BOX_ENV', 'GRACE_S', 'POLL_S', 'Claim', 'admit', 'box_path', 'holdings']

#: Environment override of the ledger file (tests; a second box on one machine).
BOX_ENV: Final = 'LAB_COMMONS_BOX'

#: Seconds past its deadline a claim is still honoured: the enforcer's kill (phase 2) needs a
#: moment to land before the room is lent again.
GRACE_S: Final = 60.0

#: Seconds between a waiter's looks at the ledger.
POLL_S: Final = 0.5

#: How long one transaction may wait for the write lock; a decision holds it for milliseconds.
_LOCK_S: Final = 30.0

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL,
    deadline REAL NOT NULL, started REAL NOT NULL, what TEXT NOT NULL, key TEXT, fingerprint TEXT);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,
    gives_up REAL NOT NULL, what TEXT NOT NULL);
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


@dataclass(frozen=True)
class Claim:
    """An admitted run's row: its id in the ledger and the moment its time limit ends."""

    id: int
    deadline: float


def _connect() -> sqlite3.Connection:
    path = box_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=_LOCK_S, isolation_level=None)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(_SCHEMA)
    present = {row[1] for row in con.execute('PRAGMA table_info(claims)')}
    for added in ('ALTER TABLE claims ADD COLUMN key TEXT', 'ALTER TABLE claims ADD COLUMN fingerprint TEXT'):
        if added.split()[-2] not in present:
            con.execute(added)
    return con


@contextmanager
def _immediate(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute('BEGIN IMMEDIATE')
    try:
        yield con
    except BaseException:
        con.execute('ROLLBACK')
        raise
    con.execute('COMMIT')


def _read(con: sqlite3.Connection) -> list[Holding]:
    rows = con.execute('SELECT what, pid, cpu, mem_bytes, seats, deadline FROM claims ORDER BY id').fetchall()
    return [Holding(what, pid, cpu, mem, json.loads(seats), deadline) for what, pid, cpu, mem, seats, deadline in rows]


def holdings() -> list[Holding]:
    """Every claim in the ledger now, oldest first."""
    with closing(_connect()) as con:
        return _read(con)


#: Per table: the query listing ``(id, pid, created, end)`` and the delete of one row. Fixed text, no
#: interpolation: a claim ends at its deadline plus the grace, a ticket when its waiter gives up.
_REAPABLE: Final = (
    ('SELECT id, pid, created, deadline + ? FROM claims', 'DELETE FROM claims WHERE id = ?', GRACE_S),
    ('SELECT id, pid, created, gives_up + ? FROM tickets', 'DELETE FROM tickets WHERE id = ?', 0.0),
)


def _reap(con: sqlite3.Connection, now: float) -> None:
    """Delete every claim and ticket whose process is gone or whose time is up."""
    for listing, delete, grace in _REAPABLE:
        for row_id, pid, created, end in con.execute(listing, (grace,)).fetchall():
            if end < now or not still_the_same_process(pid, created):
                con.execute(delete, (row_id,))


def _try(
    con: sqlite3.Connection,
    ticket: int,
    capacity: Capacity,
    demand: Demand,
    what: str,
    *,
    tag: tuple[str | None, str | None],
) -> Claim | str:
    """Inside the write lock: the claim if this ticket heads the queue and fits, else why not."""
    now = time.time()
    _reap(con, now)
    head = con.execute('SELECT id, what FROM tickets ORDER BY id LIMIT 1').fetchone()
    refusal = fits(capacity, _read(con), demand)
    if refusal is not None:
        return refusal
    if head is not None and head[0] != ticket:
        return f'queued behind {head[1]!r}, which came first (strict FIFO)'
    deadline = now + demand.wall_s
    pid = os.getpid()
    cursor = con.execute(
        'INSERT INTO claims (pid, created, cpu, mem_bytes, seats, deadline, started, what, key, fingerprint) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (
            pid,
            creation_stamp(pid),
            demand.cpu,
            demand.mem_bytes,
            json.dumps(dict(demand.seats)),
            deadline,
            now,
            what,
            *tag,
        ),
    )
    con.execute('DELETE FROM tickets WHERE id = ?', (ticket,))
    return Claim(int(cursor.lastrowid or 0), deadline)


@contextmanager
def admit(
    demand: Demand,
    what: str,
    queue_s: float,
    *,
    capacity: Capacity | None = None,
    poll_s: float = POLL_S,
    key: str | None = None,
    fingerprint: str | None = None,
) -> Iterator[Claim]:
    """Hold *demand* on this box for the ``with`` body; wait for room at most *queue_s* seconds.

    *key* (the caller's opaque identity of WHAT runs) and *fingerprint* (its hash of the code the
    run depends on) are stored on the row unparsed, for the caller's learning.

    Raises:
        Refused: at once when *demand* exceeds the capacity (it can never fit), else when
            *queue_s* ran out -- naming the short dimension, its holders and their deadlines.

    """
    capacity = Capacity.read() if capacity is None else capacity
    never = fits(capacity, (), demand)
    if never is not None:
        msg = f'{what}: larger than this box, refused at once -- {never}'
        raise Refused(msg)
    with closing(_connect()) as con:
        gives_up = time.time() + queue_s
        pid = os.getpid()
        with _immediate(con):
            ticket = int(
                con.execute(
                    'INSERT INTO tickets (pid, created, gives_up, what) VALUES (?,?,?,?)',
                    (pid, creation_stamp(pid), gives_up, what),
                ).lastrowid
                or 0
            )
        try:
            claim = _wait(
                con,
                ticket,
                capacity=capacity,
                demand=demand,
                what=what,
                tag=(key, fingerprint),
                gives_up=gives_up,
                poll_s=poll_s,
            )
        finally:
            with _immediate(con):
                con.execute('DELETE FROM tickets WHERE id = ?', (ticket,))
        try:
            yield claim
        finally:
            with _immediate(con):
                con.execute('DELETE FROM claims WHERE id = ?', (claim.id,))


def _wait(
    con: sqlite3.Connection,
    ticket: int,
    *,
    capacity: Capacity,
    demand: Demand,
    what: str,
    tag: tuple[str | None, str | None],
    gives_up: float,
    poll_s: float,
) -> Claim:
    while True:
        with _immediate(con):
            outcome = _try(con, ticket, capacity, demand, what, tag=tag)
        if isinstance(outcome, Claim):
            return outcome
        left = gives_up - time.time()
        if left <= 0:
            msg = f'{what}: no room within the queue limit -- {outcome}'
            raise Refused(msg)
        time.sleep(min(poll_s, left))
