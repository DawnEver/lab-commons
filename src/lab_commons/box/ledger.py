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

A WAITER HOLDS NOTHING. It takes a FIFO ticket, carrying its demand, that orders it in the queue and
counts for nothing in the room. Queueing is STRICT FIFO AMONG CONTENDERS: a ticket is admitted only when
no earlier live ticket wants a resource it wants (cores, memory, or the same seat), so a large demand is
never starved by a stream of small ones. A demand wanting nothing the queue ahead waits on -- a seats-only
environment check, say -- takes nothing any earlier waiter is waiting for, so it passes them. The price
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

A CLAIM MAY BE TAKEN BY ONE PROCESS AND RUN BY ANOTHER: :func:`take` admits it at once (no wait) and keeps
it with the taker -- a scheduler deciding to start a run -- and the run it spawns :func:`adopted` it: the row
moves to the run's pid with no second admission, so a run its scheduler admitted is never refused by the same
box a moment later, and it is released when the run ends (or reaped with whichever process holds it).

AN EXTERNAL CLAIM'S KEY IS EXCLUSIVE: while a persisted claim under *key* lives, a new :func:`hold` under
the same key is refused at once with :class:`~lab_commons.box.demand.DuplicateKey` -- a study whose earlier
job is unresolved is not submitted twice. Each :class:`~lab_commons.box.demand.Holding` carries its ``id``,
``key`` and ``probe``, so ANY process can rebuild the claim (``holding.claim``) and :func:`release` it.

A TREE ALREADY INSIDE A CLAIM DOES NOT CLAIM AGAIN (:data:`INSIDE_ENV`, set on every boxed child): its
cores and memory are its enclosing claim's. Seats are still booked -- a licence is not a share of a tree --
and a claim for a process OUTSIDE the tree (``outside=True``, a vendor) always books.

The file is SQLite in WAL mode, every decision inside ``BEGIN IMMEDIATE``: one writer at a time
across every process on the box, so two racing admissions cannot both see the same free room.
Location (:func:`box_path`): ``$LAB_COMMONS_BOX`` if set, else on Windows
``%ProgramData%/lab-commons/box.sqlite`` -- machine-wide, because the box is shared by every
session of its user (agents, services, terminals) and one manager per MACHINE is the point;
ProgramData admits a user-created subdirectory without elevation.

AN UNTRUSTED ROW PINS NOTHING (a consumer SR-20261016-031: a planted claim with a future time pinned every
seat). Every row this code writes starts now and ends within :data:`~lab_commons.box.demand.WALL_CEILING_S`;
a row starting in the future or ending past that is reaped on sight, live pid or not. And a ledger file
another user could write is refused at open (:func:`untrusted`): its rows carry liveness probes the reaper
RUNS, so a writable ledger would be code execution, not only a pinned seat. Elsewhere
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
from datetime import UTC, datetime
from typing import Final

import lab_commons.launch as _door
from lab_commons.box._store import BOX_ENV, box_path, untrusted
from lab_commons.box._store import connect as _connect
from lab_commons.box._store import immediate as _immediate
from lab_commons.box._store import read as _read
from lab_commons.box.demand import (
    GB,
    NOT_ADMITTED,
    WALL_CEILING_S,
    Capacity,
    Claim,
    Demand,
    DuplicateKey,
    Holding,
    NotAdmitted,
    Refused,
)
from lab_commons.box.supply import Reading, room
from lab_commons.compute.admission import admit
from lab_commons.compute.model import Job, Resources, Supply
from lab_commons.liveness import creation_stamp, still_the_same_process

#: The OS reader a decision measures with: the claim pids in, a :class:`~lab_commons.box.supply.Reading` out.
Read = Callable[[Iterable[int]], Reading]

__all__ = [
    'BOX_ENV', 'CPU_ENV', 'GRACE_S', 'INSIDE_ENV', 'POLL_S', 'PROBE_S', 'adopted', 'box_path', 'hand_over', 'hold',
    'holdings', 'persist', 'release', 'take', 'untrusted',
]  # fmt: skip

#: Set on every boxed child to its run's key: a tree already inside a claim is not claimed twice.
INSIDE_ENV: Final = 'LAB_COMMONS_BOXED'

#: Set on every boxed child to the cores its claim declared: a worker pool inside sizes to them (declared == used).
CPU_ENV: Final = 'LAB_COMMONS_BOX_CPU'

#: Seconds a liveness probe of an external claim may take before it counts as "cannot say" (kept).
PROBE_S: Final = 10.0

#: Seconds past its deadline a claim is still honoured: the enforcer's kill (phase 2) needs a
#: moment to land before the room is lent again.
GRACE_S: Final = 60.0

#: Seconds between a waiter's looks at the ledger.
POLL_S: Final = 0.5


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


#: Fixed text: the rows no admission could have written -- a start in the future, an end past the wall ceiling.
_IMPOSSIBLE: Final = (
    'DELETE FROM claims WHERE started > ? + ? OR deadline > ? + ? + ?',
    'DELETE FROM tickets WHERE gives_up > ? + ? + ?',
)


def _reap(con: sqlite3.Connection, now: float) -> None:
    """Delete every claim and ticket whose process is gone or time is up; an external claim when its probe says so."""
    claims, tickets = _IMPOSSIBLE
    con.execute(claims, (now, GRACE_S, now, WALL_CEILING_S, GRACE_S))
    con.execute(tickets, (now, WALL_CEILING_S, GRACE_S))
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


def _contends(demand: Demand, ahead: tuple[int | None, int | None, str | None]) -> bool:
    """Whether an earlier ticket asking *ahead* wants any resource *demand* does; an undeclared one wants all."""
    cpu, mem_bytes, seats = ahead
    if cpu is None or mem_bytes is None or seats is None:
        return True
    wanted = json.loads(seats)
    return bool(
        (cpu and demand.cpu)
        or (mem_bytes and demand.mem_bytes)
        or any(amount and wanted.get(name) for name, amount in demand.seats.items())
    )


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
    earlier = con.execute('SELECT what FROM claims WHERE key = ? AND probe IS NOT NULL', (tag[0],)).fetchone()
    if earlier is not None:
        msg = (
            f'{what}: key {tag[0]!r} is held by the external claim {earlier[0]!r}, still unresolved -- release it first'
        )
        raise DuplicateKey(msg)
    ahead = con.execute('SELECT what, cpu, mem_bytes, seats FROM tickets WHERE id < ? ORDER BY id', (ticket,))
    head = next((row[0] for row in ahead if _contends(demand, row[1:])), None)
    refusal = _decide(capacity, _read(con), reading, demand, what)
    if refusal is not None:
        return refusal
    if head is not None:
        return f'queued behind {head!r}, which came first and wants what it wants (strict FIFO)'
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
    if not 0 <= queue_s <= WALL_CEILING_S:
        msg = f'{what}: queue_s must lie in [0, WALL_CEILING_S={WALL_CEILING_S}], got {queue_s}'
        raise ValueError(msg)
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
                    'INSERT INTO tickets (pid, created, gives_up, what, cpu, mem_bytes, seats) VALUES (?,?,?,?,?,?,?)',
                    (pid, creation_stamp(pid), gives_up, what, demand.cpu, demand.mem_bytes, _seats(demand)),
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


def take(demand: Demand, what: str, *, key: str, capacity: Capacity | None = None, read: Read = Reading.now) -> Claim:
    """Admit *demand* NOW (one look, no wait) and keep the claim past this call, for a run that :func:`adopted` it.

    The claim lives with this process until the run adopts it, :func:`release` drops it, or this process dies.

    Raises:
        NotAdmitted: the measured room does not admit it now -- nothing is held.

    """
    with hold(demand, what, 0.0, key=key, capacity=capacity, read=read) as claim:
        _update(claim, 'UPDATE claims SET kept = 1 WHERE id = ?')
    return claim


@contextmanager
def adopted(claim_id: int) -> Iterator[None]:
    """Hold the claim another process :func:`take`-s for the ``with`` body: no second admission, released after.

    Raises:
        Refused: no claim *claim_id* is on this box's ledger (released, or reaped with its taker).

    """
    if all(one.id != claim_id for one in holdings()):
        msg = f'claim {claim_id} is not on this box ledger -- nothing to adopt'
        raise Refused(msg)
    hand_over(Claim(claim_id, 0.0), os.getpid())
    try:
        yield
    finally:
        release(Claim(claim_id, 0.0))


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
            msg = f'{what}: {NOT_ADMITTED} -- no room within the queue limit -- {outcome}'
            raise NotAdmitted(msg)
        time.sleep(min(poll_s, left))


def _seats(demand: Demand) -> str:
    return json.dumps(dict(demand.seats))
