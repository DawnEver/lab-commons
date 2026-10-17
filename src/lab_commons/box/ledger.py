"""The ONE ledger of running claims on this machine, and the bounded wait to hold one.

THE DECISION IS THE ONE RULE ON THE MEASURED ROOM. A claim is written when
:func:`lab_commons.compute.admission.admit` admits its demand against :func:`lab_commons.box.supply.room` --
what the OS has free now, less what the claims already held have not taken yet, less the ``[reserve]``
margin (D2, D3 2026-10-16). There is no second fit rule here.

A CLAIM EXISTS ONLY FOR A RUNNING PROCESS. It is written when the launcher is admitted, deleted
when it leaves, and equals the run's declared :class:`~lab_commons.box.demand.Demand` -- which
phase 2 makes the kernel limit of that process. A claim therefore cannot be idle-but-held: what
it reserves is exactly what its process is allowed to use, and it ends no later than its
deadline. A row whose launcher is gone (pid absent, or its creation stamp differs: a recycled
pid) or whose deadline passed by more than :data:`GRACE_S` is reaped by the next admission, so
no crash leaves memory booked.

A WAITER HOLDS NOTHING. It takes a FIFO ticket that orders it in the queue and counts for
nothing in the room. Queueing is STRICT FIFO: only the oldest live
ticket may be admitted, so a large demand is never starved by a stream of small ones. The price
is head-of-line blocking, and it is bounded on both sides, which is why EVERY WAIT ENDS: the head
waits at most until the holders' deadlines (each claim ends by then), and every waiter gives up
at its own ``queue_s`` -- a required argument -- and its ticket is reaped with it.

A CLAIM MAY OUTLIVE ITS LAUNCHER IN TWO NAMED WAYS, and only those. :func:`hand_over` makes a vendor
process (a Designer, a solver started outside this tree) the claim's holder: the claim then lives and is
reaped with THAT process. :func:`persist` makes it EXTERNAL -- a job queued in a vendor scheduler, with no
process of ours to watch: it outlives its client until :func:`release` is called on terminal evidence, its
deadline passes, or its LIVENESS PROBE (an argv run by the reaper once its client is gone; exit 0 alive,
anything else dead) says the job is gone. Without the probe a dead job's claim held the box until its
deadline (the open defect "an external claim outlives its dead job").

A TREE ALREADY INSIDE A CLAIM DOES NOT CLAIM AGAIN (:data:`INSIDE_ENV`, set on every boxed child): its
cores and memory are its enclosing claim's. Seats are still booked -- a licence is not a share of a tree --
and a claim for a process OUTSIDE the tree (``outside=True``, a vendor) always books.

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
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons.box.demand import GB, Capacity, Demand, Holding, Refused
from lab_commons.box.supply import Reading, room
from lab_commons.compute.admission import admit
from lab_commons.compute.model import Job, Resources, Supply
from lab_commons.liveness import creation_stamp, still_the_same_process

#: The OS reader a decision measures with: the claim pids in, a :class:`~lab_commons.box.supply.Reading` out.
Read = Callable[[Iterable[int]], Reading]

__all__ = [
    'BOX_ENV', 'GRACE_S', 'INSIDE_ENV', 'POLL_S', 'PROBE_S', 'Claim', 'box_path', 'hand_over', 'hold', 'holdings',
    'persist', 'release',
]  # fmt: skip

#: Set on every boxed child to its run's key: a tree already inside a claim is not claimed twice.
INSIDE_ENV: Final = 'LAB_COMMONS_BOXED'

#: Seconds a liveness probe of an external claim may take before it counts as "cannot say" (kept).
PROBE_S: Final = 10.0

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
    deadline REAL NOT NULL, started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT,
    kept INTEGER NOT NULL DEFAULT 0, probe TEXT);
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
    columns = {row[1] for row in con.execute('PRAGMA table_info(claims)')}
    for name, spelled in _ADDED:
        if name not in columns:  # a ledger file written before the column existed: the schema moves forward
            con.execute(f'ALTER TABLE claims ADD COLUMN {spelled}')
    return con


#: Columns added to ``claims`` after the file format was first written: (name, its fixed definition).
_ADDED: Final = (('kept', 'kept INTEGER NOT NULL DEFAULT 0'), ('probe', 'probe TEXT'))


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
    """Delete every claim and ticket whose process is gone or time is up; an external claim when its probe says so."""
    probes = dict(con.execute('SELECT id, probe FROM claims WHERE probe IS NOT NULL').fetchall())
    for table, (listing, delete, grace) in zip(('claims', 'tickets'), _REAPABLE, strict=True):
        for row_id, pid, created, end in con.execute(listing, (grace,)).fetchall():
            if end >= now and still_the_same_process(pid, created):
                continue
            external = table == 'claims' and row_id in probes
            if end < now or not external or not _alive(json.loads(probes[row_id])):
                con.execute(delete, (row_id,))


def _alive(probe: Sequence[str]) -> bool:
    """Whether an external job's liveness *probe* says it still runs: exit 0. A probe that cannot say keeps it."""
    try:
        return _door.run(list(probe), capture_output=True, timeout=PROBE_S, check=False).returncode == 0
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return False


def _update(claim: Claim, sql: str, *values: object) -> None:
    with closing(_connect()) as con, _immediate(con):
        con.execute(sql, (*values, claim.id))


def hand_over(claim: Claim, pid: int) -> None:
    """Make process *pid* (a vendor started outside this tree) the holder of *claim*: it lives and is reaped with it."""
    _update(claim, 'UPDATE claims SET pid = ?, created = ?, kept = 1 WHERE id = ?', pid, creation_stamp(pid))


def persist(claim: Claim, probe: Sequence[str]) -> None:
    """Keep *claim* past its client: an EXTERNAL job, gone at :func:`release`, its deadline, or when *probe* fails."""
    _update(claim, 'UPDATE claims SET kept = 1, probe = ? WHERE id = ?', json.dumps(list(probe)))


def release(claim: Claim) -> None:
    """Drop *claim* now, on terminal evidence of the job it holds for."""
    _update(claim, 'DELETE FROM claims WHERE id = ?')


def _vector(demand: Demand) -> Resources:
    return Resources({'cpu': demand.cpu, 'mem_gb': demand.mem_bytes / GB, **demand.seats})


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec='seconds')


def _decide(capacity: Capacity, held: Sequence[Holding], reading: Reading, demand: Demand, what: str) -> str | None:
    """``None`` when THE RULE admits *demand* on the measured room, else why not -- naming each lacking holder."""
    free = room(capacity, held, reading, seats=demand.seats)
    verdict = admit(Job(repo='', sha='', producer=what), _vector(demand), Supply('box', free, free.names | {'cpu'}))
    if verdict:
        return None
    holders = [
        f'{h.what} (pid {h.pid}) holds {amount:g} until {_when(h.deadline)}'
        for name in verdict.lacking
        for h in held
        if (amount := _vector_of(h)[name])
    ]
    return f'{verdict.why}; ' + (', '.join(holders) or 'held by nobody on the ledger -- the rest is in use outside it')


def _vector_of(held: Holding) -> Resources:
    return Resources({'cpu': held.cpu, 'mem_gb': held.mem_bytes / GB, **held.seats})


def _try(
    con: sqlite3.Connection,
    ticket: int,
    capacity: Capacity,
    demand: Demand,
    what: str,
    *,
    reading: Reading,
    tag: tuple[str, str | None],
) -> Claim | str:
    """Inside the write lock: the claim if this ticket heads the queue and the measured room admits it, else why not.

    *reading* was taken BEFORE the lock (a sample takes a moment, the lock is held for milliseconds); a claim
    that arrived since is in the ledger read here with no usage in it, so it counts in full: a stale reading
    only ever lends less.
    """
    now = time.time()
    _reap(con, now)
    head = con.execute('SELECT id, what FROM tickets ORDER BY id LIMIT 1').fetchone()
    refusal = _decide(capacity, _read(con), reading, demand, what)
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
def hold(
    demand: Demand,
    what: str,
    queue_s: float,
    *,
    capacity: Capacity | None = None,
    read: Read = Reading.now,
    poll_s: float = POLL_S,
    key: str,
    fingerprint: str | None = None,
    outside: bool = False,
) -> Iterator[Claim]:
    """Hold *demand* on this box for the ``with`` body; wait for room at most *queue_s* seconds.

    *capacity* is the box's size (default: :meth:`Capacity.read`), *read* the OS reader the room is measured
    with (default: :meth:`Reading.now`). *key* (the caller's opaque identity of WHAT runs) and *fingerprint*
    (its hash of the code the run depends on) are stored on the row unparsed, for the caller's learning.

    Raises:
        Refused: at once when *demand* exceeds the box's size (it can never fit), else when
            *queue_s* ran out -- naming what is lacking, its holders and their deadlines.

    """
    if INSIDE_ENV in os.environ and not outside and not any(demand.seats.values()):
        yield Claim(0, float('inf'))  # this tree's enclosing claim already holds it
        return
    capacity = Capacity.read() if capacity is None else capacity
    size = Resources(
        {'cpu': capacity.cpu, 'mem_gb': capacity.mem_bytes / GB, **{n: capacity.seats.get(n, 1) for n in demand.seats}}
    )
    never = admit(Job(repo='', sha='', producer=what), _vector(demand), Supply('box', size, size.names | {'cpu'}))
    if not never:
        msg = f'{what}: larger than this box, refused at once -- {never.why}'
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
                read=read,
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
                con.execute('DELETE FROM claims WHERE id = ? AND kept = 0', (claim.id,))


def _wait(
    con: sqlite3.Connection,
    ticket: int,
    *,
    capacity: Capacity,
    demand: Demand,
    what: str,
    read: Read,
    tag: tuple[str, str | None],
    gives_up: float,
    poll_s: float,
) -> Claim:
    while True:
        reading = read([h.pid for h in _read(con)])
        with _immediate(con):
            outcome = _try(con, ticket, capacity, demand, what, reading=reading, tag=tag)
        if isinstance(outcome, Claim):
            return outcome
        left = gives_up - time.time()
        if left <= 0:
            msg = f'{what}: no room within the queue limit -- {outcome}'
            raise Refused(msg)
        time.sleep(min(poll_s, left))
