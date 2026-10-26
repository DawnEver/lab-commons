"""THE ENGINE -- the one path every claim of every kind takes: admit, hold, charge, release, reap.

Providers only MEASURE and ENFORCE; the engine holds, admits, charges, releases and reaps, on the one ledger
(the box ledger file, :mod:`lab_commons.box._store`): a held amount is a ``claims`` row keyed by its ``scope``,
``kind`` and -- for a consumable -- its ``window``; a consumable's spent amount is a ``charges`` row that counts
in its window and is dropped after it. The decision is :func:`lab_commons.compute.admission.admit` on the
scope's summed supply less every row still held on it, inside ``BEGIN IMMEDIATE``, so two racing claims never
see the same free amount. A held consumable cap is a promise: it counts until the run ends.

A consumable's WINDOW is the machine file's ``[resources.<name>] window`` (``"1d"``, ``"12h"``, ``"30m"``,
``"45s"``); a consumable claimed without one is refused. The provider's ``measure`` computes the balance
(cap less used in the window), reading the engine's local spend with :func:`charged`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from typing import Final

from lab_commons.box._store import connect, immediate
from lab_commons.box.demand import GB, NOT_ADMITTED, NotAdmitted
from lab_commons.box.ledger import reap as reap_ledger
from lab_commons.compute.admission import Admission, admit
from lab_commons.compute.model import Job, Resources, Supply, total
from lab_commons.config import section
from lab_commons.liveness import creation_stamp
from lab_commons.resource.model import Actual, Claim, Kind, Provider, kind_of

__all__ = ['Engine', 'Held', 'charged', 'window_s']

_UNITS: Final = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
_WINDOW: Final = re.compile(r'^(?P<n>\d+(?:\.\d+)?)(?P<unit>[smhd])$')


def window_s(name: str) -> tuple[str, float]:
    """The consumable *name*'s window as declared and in seconds.

    Raises:
        ValueError: no ``[resources.<name>] window`` is declared, or it is malformed.

    """
    spelled = section('resources').get(name, {}).get('window')
    parsed = _WINDOW.match(spelled) if isinstance(spelled, str) else None
    if parsed is None:
        msg = f'consumable {name!r} needs a machine file [resources.{name}] window like "1d", got {spelled!r}'
        raise ValueError(msg)
    return spelled, float(parsed['n']) * _UNITS[parsed['unit']]


def charged(scope: str, name: str, *, now: float | None = None) -> float:
    """What this ledger charged *name* on *scope* within its window ending *now*: the local half of a balance."""
    now = time.time() if now is None else now
    with closing(connect()) as con:
        row = con.execute(
            'SELECT TOTAL(amount) FROM charges WHERE scope = ? AND name = ? AND at + window_s >= ?', (scope, name, now)
        ).fetchone()
    return float(row[0])


@dataclass(frozen=True)
class Held:
    """A claim the engine holds on *scope*: its ledger row per kind."""

    scope: str
    claim: Claim
    rows: Mapping[Kind, int]


def _vector(cpu: float, mem: int, rest: str) -> Resources:
    return Resources({'cpu': cpu, 'mem_gb': mem / GB, **json.loads(rest)})


def _row(demand: Mapping[str, float]) -> tuple[float, int, str]:
    """A demand as the ledger columns spell it: cores, bytes, and every other name as JSON."""
    rest = {name: amount for name, amount in demand.items() if name not in {'cpu', 'mem_gb'}}
    return demand.get('cpu', 0.0), int(demand.get('mem_gb', 0.0) * GB), json.dumps(rest)


class Engine:
    """The one path, over the providers registered per scope."""

    def __init__(self, providers: Mapping[str, Iterable[Provider]]) -> None:
        """Register *providers* per scope; refuse a name with no kind or two providers of one name on one scope.

        Raises:
            ValueError: naming the name and the scope.

        """
        self._providers: dict[str, tuple[Provider, ...]] = {}
        for scope, plugs in providers.items():
            seen: set[str] = set()
            for plug in (plugs := tuple(plugs)):
                for name in plug.names:
                    kind_of(name)
                twice = seen & plug.names
                if twice:
                    msg = f'scope {scope!r}: {sorted(twice)} supplied by two providers'
                    raise ValueError(msg)
                seen |= plug.names
            self._providers[scope] = plugs

    def supply(self, scope: str) -> Supply:
        """Every provider's measure on *scope*, summed per name, each provider counted on its own names only."""
        measured = [plug.measure(scope) for plug in self._plugs(scope)]
        vector = total(
            Resources({n: m.resources[n] for n in plug.names})
            for plug, m in zip(self._plugs(scope), measured, strict=True)
        )
        return Supply(scope, vector, frozenset().union(*(m.present for m in measured)))

    def free(self, scope: str) -> Resources:
        """What *scope* could admit now: its supply less every claim still held on it."""
        with closing(connect()) as con:
            return self.supply(scope).resources - total(_held(con, scope))

    def claimable(self, scope: str, claim: Claim) -> Admission:
        """THE RULE on *scope* now for *claim*, holding nothing."""
        with closing(connect()) as con:
            return self._admit(con, scope, claim)

    @contextmanager
    def claim(self, scope: str, claim: Claim, *, probe: Sequence[str] = ()) -> Iterator[Held]:
        """Hold *claim* on *scope* for the ``with`` body, run within its providers' enforcement; then :meth:`charge`.

        A PERSISTENT part stays held past the body, kept with liveness *probe* (empty: until its deadline), and
        returns only by :meth:`reap`.

        Raises:
            NotAdmitted: the rule refuses it now, naming each name lacking with asked and free.
            ValueError: a consumable with no declared window.

        """
        held = self._hold(scope, claim, probe)
        plugs = [plug for plug in self._plugs(scope) if plug.names & claim.demand.names]
        actuals: list[Actual] = []
        with ExitStack() as stack:
            stack.callback(lambda: self.charge(held, total(a.used for a in actuals)))
            actuals.extend(stack.enter_context(plug.enforce(scope, claim)) for plug in plugs)
            yield held

    def charge(self, held: Held, used: Mapping[str, float]) -> None:
        """End *held*'s run: charge its consumables' *used* amounts, return occupancy and unspent caps now."""
        now = time.time()
        with closing(connect()) as con, immediate(con):
            for name in held.claim.demand:
                if kind_of(name) is Kind.CONSUMABLE and used.get(name, 0.0):
                    _, seconds = window_s(name)
                    con.execute(
                        'INSERT INTO charges (scope, name, amount, at, window_s, what) VALUES (?,?,?,?,?,?)',
                        (held.scope, name, used[name], now, seconds, held.claim.what),
                    )
            for kind, row_id in held.rows.items():
                if kind is not Kind.PERSISTENT:
                    con.execute('DELETE FROM claims WHERE id = ?', (row_id,))

    def reap(self, *, now: float | None = None) -> None:
        """Drop every row whose holder is gone or deadline passed (the ledger reap), and each charge past its window."""
        now = time.time() if now is None else now
        reap_ledger(now)
        with closing(connect()) as con, immediate(con):
            con.execute('DELETE FROM charges WHERE at + window_s < ?', (now,))

    def _plugs(self, scope: str) -> tuple[Provider, ...]:
        if scope not in self._providers:
            msg = f'scope {scope!r} has no provider registered'
            raise ValueError(msg)
        return self._providers[scope]

    def _admit(self, con: sqlite3.Connection, scope: str, claim: Claim) -> Admission:
        return admit(
            Job(repo='', sha='', producer=claim.what), claim.demand, self.supply(scope), running=_held(con, scope)
        )

    def _hold(self, scope: str, claim: Claim, probe: Sequence[str]) -> Held:
        parts: dict[tuple[Kind, str | None], dict[str, float]] = {}
        for name, amount in claim.demand.items():
            kind = kind_of(name)
            window = window_s(name)[0] if kind is Kind.CONSUMABLE else None
            parts.setdefault((kind, window), {})[name] = amount
        if len({kind for kind, _ in parts}) < len(parts):
            msg = f'{claim.what}: its consumables declare different windows -- claim them apart'
            raise ValueError(msg)
        self.reap()
        now, pid = time.time(), os.getpid()
        with closing(connect()) as con, immediate(con):
            verdict = self._admit(con, scope, claim)
            if not verdict:
                msg = f'{claim.what}: {NOT_ADMITTED} on {scope} -- {verdict.why}'
                raise NotAdmitted(msg)
            rows = {}
            for (kind, window), demand in parts.items():
                persistent = kind is Kind.PERSISTENT
                cursor = con.execute(
                    'INSERT INTO claims (pid, created, cpu, mem_bytes, seats, deadline, started, what, key, kept, '
                    'probe, scope, kind, window) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        pid,
                        creation_stamp(pid),
                        *_row(demand),
                        now + claim.wall_s,
                        now,
                        claim.what,
                        claim.key,
                        int(persistent),
                        json.dumps(list(probe)) if persistent else None,
                        scope,
                        kind.value,
                        window,
                    ),
                )
                rows[kind] = int(cursor.lastrowid or 0)
        return Held(scope, claim, rows)


def _held(con: sqlite3.Connection, scope: str) -> list[Resources]:
    rows = con.execute('SELECT cpu, mem_bytes, seats FROM claims WHERE scope = ?', (scope,)).fetchall()
    return [_vector(*row) for row in rows]
