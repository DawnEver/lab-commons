"""How much of the pool a SESSION may hold -- derived from Slurm, or overridden by one expiring forge record.

AN ALLOCATION BETWEEN SESSIONS IS TEMPORARY AND NEVER LIVES IN A MACHINE FILE (user ruling 2026-10-17).

THE DEFAULT IS DERIVED. The pool is MEASURED: the sum of every account's ``GrpTRES`` ceiling
(``sacctmgr``; Ada: ``cpu=96, mem=360G`` per account). The sessions sharing it are the ACTIVE ones -- each
tag holding pending or running tasks in ``squeue -r`` on any account (:func:`active`); no registry, no
heartbeat, Slurm is the source of truth. Jobs without a tag are one more stream, :data:`UNTAGGED` (the forge
alarms on them). The pool is divided by MAX-MIN FAIRNESS, work-conserving (:func:`fair`): every other stream
is held to what it holds now, the asking session's demand is unbounded, so an idle session takes nothing
and its share lends out, and two busy sessions meet at equal halves. Past its share a session's new
submissions WAIT (:func:`session_room` gives them no room); nothing is ever cancelled.

THE OVERRIDE IS ONE RECORD, :data:`RECORD`, on the forge's records home (``python -m lab_commons.hpc
allocate``): ``{"expires": <UTC iso>, "sessions": {name: {cpus, mem_gb, tasks?, task_cpus?, task_mem_gb?}}}``.
While it is unexpired it FIXES the named sessions' shares; the remainder -- the pool less what each named
session uses of its share, floored at 0 -- is divided by the same max-min among the unnamed active streams,
untagged included, so a named session's slack lends out exactly as a derived share's does. Expired
or absent, the derived default stands. Every submitter -- any agent, any tool -- reads the same record
under its own ``$LC_HPC_SESSION``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final

from lab_commons.hpc.config import Cost
from lab_commons.hpc.grants import Session
from lab_commons.hpc.slurm import Held, Snapshot

__all__ = [
    'RECORD',
    'UNTAGGED',
    'Allocation',
    'active',
    'derived',
    'fair',
    'parse',
    'parse_shares',
    'pool',
    'render',
    'session_for',
    'session_room',
]

#: The one override record's name on the forge records home.
RECORD: Final = 'hpc-allocation.json'

#: The stream every job without a session tag counts as.
UNTAGGED: Final = 'untagged'


@dataclass(frozen=True)
class Allocation:
    """The override: each named session's caps, until *expires* (UTC)."""

    expires: datetime
    sessions: Mapping[str, Session]


def parse(text: str) -> Allocation:
    """:data:`RECORD`'s JSON; a malformed record is refused by name."""
    raw = json.loads(text)
    expires = datetime.fromisoformat(raw['expires'])
    if expires.tzinfo is None:
        msg = f'{RECORD}: expires {raw["expires"]!r} names no timezone; write it in UTC'
        raise ValueError(msg)
    return Allocation(expires, {name: Session(name, **caps) for name, caps in raw['sessions'].items()})


def render(allocation: Allocation) -> str:
    """The record's JSON; an unset optional cap is left out."""
    sessions = {
        name: {k: v for k, v in asdict(session).items() if k not in {'name', 'fixed'} and v is not None}
        for name, session in sorted(allocation.sessions.items())
    }
    return json.dumps({'expires': allocation.expires.astimezone(UTC).isoformat(), 'sessions': sessions}, indent=2)


def parse_shares(words: Sequence[str]) -> dict[str, Session]:
    """``name=cpus/mem_gb`` words into sessions."""
    shares = {}
    for word in words:
        name, _, caps = word.partition('=')
        cpus, _, mem_gb = caps.partition('/')
        try:
            shares[name] = Session(name, cpus=float(cpus), mem_gb=float(mem_gb))
        except ValueError as bad:
            msg = f'{word!r}: write each share as name=cpus/mem_gb ({bad})'
            raise ValueError(msg) from bad
    return shares


def session_for(name: str | None, record: str | None, *, now: datetime) -> Session | None:
    """*name*'s session: the record's caps while it is unexpired (unnamed: derived from the remainder), else derived."""
    if name is None:
        return None
    if record is None:
        return Session(name)
    allocation = parse(record)
    if now >= allocation.expires:
        return Session(name)
    if name in allocation.sessions:
        return allocation.sessions[name]
    fixed = tuple((n, x.cpus or 0.0, x.mem_gb or 0.0) for n, x in sorted(allocation.sessions.items()))
    return Session(name, fixed=fixed)


def active(snapshots: Sequence[Snapshot]) -> dict[str, Held]:
    """What each stream holds, pending and running, across every account; untagged jobs are :data:`UNTAGGED`."""
    held: dict[str, Held] = {}
    for snapshot in snapshots:
        for tag, usage in snapshot.usage.items():
            name = tag or UNTAGGED
            held[name] = held.get(name, Held()) + usage
    return held


def pool(snapshots: Sequence[Snapshot]) -> tuple[float, float]:
    """The pool's CPUs and memory (MB): every account's measured ceiling summed; an unbounded one is ``inf``."""
    cpus = [s.quota.cpus for s in snapshots]
    mem = [s.quota.mem_mb for s in snapshots]
    return (
        math.inf if None in cpus else sum(c for c in cpus if c is not None),
        math.inf if None in mem else sum(m for m in mem if m is not None),
    )


def fair(capacity: float, demands: Mapping[str, float]) -> dict[str, float]:
    """Max-min fair shares of *capacity*: the smallest demands are met in full, the rest split what is left."""
    if math.isinf(capacity):
        return dict(demands)
    shares: dict[str, float] = {}
    left = capacity
    waiting = sorted(demands, key=lambda name: demands[name])
    while waiting:
        each = left / len(waiting)
        name = waiting[0]
        if demands[name] > each:
            shares.update(dict.fromkeys(waiting, each))
            break
        shares[name] = demands[name]
        left -= demands[name]
        waiting.pop(0)
    return shares


def derived(
    name: str, snapshots: Sequence[Snapshot], fixed: Sequence[tuple[str, float, float]] = ()
) -> tuple[float, float]:
    """*name*'s derived caps -- CPUs and memory (GB) -- its max-min share among the active streams.

    The share is of the pool less what each *fixed* (named) session USES of its fixed share, floored at 0.
    """
    held = active(snapshots)
    named = {n: (cpus, mem_gb) for n, cpus, mem_gb in fixed}
    cpus, mem_mb = pool(snapshots)
    for n, (cap_cpus, cap_gb) in named.items():
        used = held.get(n, Held())
        cpus = max(0.0, cpus - min(cap_cpus, used.cpus))
        mem_mb = max(0.0, mem_mb - min(cap_gb * 1024, used.mem_mb))
    others = {tag: h for tag, h in held.items() if tag != name and tag not in named}
    by_cpu = fair(cpus, {name: math.inf, **{tag: h.cpus for tag, h in others.items()}})
    by_mem = fair(mem_mb, {name: math.inf, **{tag: h.mem_mb for tag, h in others.items()}})
    return by_cpu[name], by_mem[name] / 1024


def session_room(session: Session | None, snapshots: Sequence[Snapshot], cost: Cost) -> int | None:
    """Tasks of *cost* *session* may still start: its share less what its tag holds on EVERY account; ``None`` uncapped.

    The accounts are one pool (user ruling 2026-10-17), so the session is counted across all of them, pending
    tasks included. Its share is the override record's caps, else derived from Slurm now
    (:func:`derived`). A task past a per-task ceiling is refused; one that fits is
    never refused, only held to 0.
    """
    if session is None:
        return None
    over = [
        f'{name} {asked:g} > {ceiling:g}'
        for name, asked, ceiling in (
            ('cpus', cost.cpus, session.task_cpus),
            ('mem_gb', cost.mem_gb, session.task_mem_gb),
        )
        if ceiling is not None and asked > ceiling
    ]
    if over:
        msg = f'session {session.name!r}: one task asks past its per-task ceiling ({", ".join(over)})'
        raise ValueError(msg)
    cpus, mem_gb = (session.cpus, session.mem_gb)
    if cpus is None or mem_gb is None:
        cpus, mem_gb = derived(session.name, snapshots, session.fixed)
    held = sum((s.usage.get(session.name, Held()) for s in snapshots), Held())
    left = [
        math.inf if session.tasks is None else session.tasks - held.tasks,
        (cpus - held.cpus) // cost.cpus if cost.cpus else math.inf,
        (mem_gb * 1024 - held.mem_mb) // (cost.mem_gb * 1024) if cost.mem_gb else math.inf,
    ]
    least = min(left)
    return None if math.isinf(least) else max(0, int(least))
