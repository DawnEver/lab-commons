"""An append-only JSONL record of what happened to each identified task.

One event per line: ``submitted``, ``outcome`` (carrying a :class:`Cell`, and the task's declared
:class:`~lab_commons.outcomes.Expectation` when it declares one), or a caller's own kind.
Each append is flushed and fsynced before :meth:`Record.append` returns, so an event that returned is
on disk. Idempotence protects against REPLAYING THE SAME EVENT, not against a new attempt that
happens to produce the same result: every outcome carries an ``attempt`` (1, 2, ... per identity;
:meth:`Record.outcome` defaults it to the next one), so a re-append of an attempt already recorded
with the same cell writes nothing, while a new attempt is always recorded and counted. Any other
event equal to one already recorded also writes nothing.

FINALITY: per identity the first DECIDED outcome is final. A later outcome with the same cell writes
nothing, whatever its attempt; one that differs -- another decision or a RETRYABLE one -- is refused
with :class:`FinalityError`. A RETRYABLE outcome is superseded by whatever comes next. Re-appending a
recorded attempt with a DIFFERENT cell, or skipping an attempt number, is refused with ``ValueError``.

ONE WRITER PER FILE. The views are built from the file at open and from this object's own appends; a
second writer's appends are not seen until the record is reopened.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from lab_commons.outcomes.cell import Cell
from lab_commons.outcomes.expectation import Expectation
from lab_commons.outcomes.identity import Identity
from lab_commons.outcomes.jsonl import read_jsonl
from lab_commons.outcomes.status import Status

__all__ = ['OUTCOME', 'SCHEMA_VERSION', 'SUBMITTED', 'Event', 'FinalityError', 'Record', 'UnknownSchema']

#: The event-line format version, written into every line.
SCHEMA_VERSION: Final = 3


def _v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 -> 2: a v1 line is a v2 line without the optional ``attempt``; only the version moves."""
    return {**raw, 'schema': 2}


def _v2_to_v3(raw: dict[str, Any]) -> dict[str, Any]:
    """Schema 2 -> 3: a v2 line is a v3 line without the optional ``expectation``; only the version moves."""
    return {**raw, 'schema': 3}


#: Each older version this build reads, and the step taking it one version on (FORMATS-UPGRADE-ON-READ:
#: a Record is LIVE state, appended to across a change).
#: v1 -> v2: v1 lines carry no ``attempt``; :class:`Record` numbers their outcomes 1.. in file order.
#: v2 -> v3: v2 outcomes carry no ``expectation``; they declared none.
_UPGRADES: Final[dict[int, Any]] = {1: _v1_to_v2, 2: _v2_to_v3}

#: The two kinds this module gives meaning to; any other kind is a caller's and is only recorded.
SUBMITTED: Final = 'submitted'
OUTCOME: Final = 'outcome'


class FinalityError(ValueError):
    """An outcome conflicts with the identity's first DECIDED outcome, which is final."""


class UnknownSchema(ValueError):
    """An event line names a schema this build neither writes nor can upgrade."""


@dataclass(frozen=True)
class Event:
    """One line: its *kind*, the identity *key*, an outcome's *cell*, *attempt* and *expectation*, and caller *data*.

    An outcome's *attempt* may be None only until a :class:`Record` numbers it; its *expectation* is None
    when the task declares none (it is expected ``OK``).
    """

    kind: str
    key: str
    cell: Cell | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    attempt: int | None = None
    expectation: Expectation | None = None

    def __post_init__(self) -> None:
        """An outcome carries a cell, and nothing else does."""
        if (self.kind == OUTCOME) != (self.cell is not None):
            msg = f'a {self.kind!r} event {"needs" if self.kind == OUTCOME else "carries no"} cell'
            raise ValueError(msg)
        if self.attempt is not None and (self.cell is None or self.attempt < 1):
            msg = f'a {self.kind!r} event cannot carry attempt {self.attempt!r}'
            raise ValueError(msg)
        if self.expectation is not None and self.cell is None:
            msg = f'a {self.kind!r} event cannot carry an expectation: only an outcome is judged against one'
            raise ValueError(msg)

    def to_plain(self) -> dict[str, Any]:
        """The JSON-ready line, schema first."""
        plain: dict[str, Any] = {'schema': SCHEMA_VERSION, 'kind': self.kind, 'key': self.key}
        if self.cell is not None:
            plain['cell'] = self.cell.to_plain()
        if self.attempt is not None:
            plain['attempt'] = self.attempt
        if self.expectation is not None:
            plain['expectation'] = self.expectation.to_plain()
        if self.data:
            plain['data'] = dict(self.data)
        return plain

    @classmethod
    def from_plain(cls, plain: Mapping[str, Any]) -> Event:
        """The inverse of :meth:`to_plain`, upgrading an older schema first; an unknown one is refused."""
        raw = dict(plain)
        while raw.get('schema') in _UPGRADES:
            raw = _UPGRADES[raw['schema']](raw)
        if raw.get('schema') != SCHEMA_VERSION:
            msg = f'event schema {raw.get("schema")!r} is not {SCHEMA_VERSION} and has no upgrade'
            raise UnknownSchema(msg)
        cell = raw.get('cell')
        expectation = raw.get('expectation')
        return cls(
            raw['kind'],
            raw['key'],
            None if cell is None else Cell.from_plain(cell),
            raw.get('data', {}),
            raw.get('attempt'),
            None if expectation is None else Expectation.from_plain(expectation),
        )


def _key(identity: Identity | str) -> str:
    return identity.key if isinstance(identity, Identity) else identity


class Record:
    """The record at *path*: append events, read views, replay in order. See the module docstring."""

    def __init__(self, path: Path) -> None:
        """Open *path* (absent is empty); a torn last line is cut off before the first append."""
        self.path = path
        read = read_jsonl(path)
        self._events = [Event.from_plain(row) for row in read.rows]
        self._good_bytes = read.good_bytes
        self._torn = read.torn
        self._submitted: dict[str, None] = {}
        self._latest: dict[str, Cell] = {}
        self._attempts: dict[str, list[Cell]] = {}
        events, self._events = self._events, []
        for event in events:
            numbered = self._numbered(event)
            self._events.append(numbered)
            self._apply(numbered)

    def _numbered(self, event: Event) -> Event:
        """*event*, an un-numbered outcome given the identity's next attempt (v1 lines, default calls)."""
        if event.cell is None or event.attempt is not None:
            return event
        return replace(event, attempt=self.attempts(event.key) + 1)

    def _apply(self, event: Event) -> None:
        self._submitted.setdefault(event.key)
        if event.cell is not None:
            self._latest[event.key] = event.cell
            self._attempts.setdefault(event.key, []).append(event.cell)

    def _admit(self, event: Event) -> bool:
        """False for a repeat; raise for an outcome conflicting with a final decision or its attempt."""
        if event.cell is None:
            return event not in self._events
        current = self._latest.get(event.key)
        if current is not None and current.status.decided:
            if current == event.cell:
                return False
            msg = f'{event.key}: {current.status.name} is final; refusing {event.cell.status.name}'
            raise FinalityError(msg)
        recorded = self._attempts.get(event.key, [])
        attempt = event.attempt or len(recorded) + 1
        if attempt <= len(recorded):
            if recorded[attempt - 1] == event.cell:
                return False
            msg = f'{event.key}: attempt {event.attempt} is recorded with a different cell'
            raise ValueError(msg)
        if attempt != len(recorded) + 1:
            msg = f'{event.key}: attempt {event.attempt} skips past {len(recorded)} recorded'
            raise ValueError(msg)
        return True

    def append(self, event: Event) -> bool:
        """Write *event* durably; False when it is already recorded (nothing written).

        An outcome with no attempt is numbered as the identity's next attempt.
        """
        event = self._numbered(event)
        if not self._admit(event):
            return False
        line = (json.dumps(event.to_plain(), sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('ab') as handle:
            if self._torn:
                handle.truncate(self._good_bytes)
                self._torn = False
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._good_bytes += len(line)
        self._events.append(event)
        self._apply(event)
        return True

    def submit(self, identity: Identity | str, data: Mapping[str, Any] | None = None) -> bool:
        """Record that a task was submitted."""
        return self.append(Event(SUBMITTED, _key(identity), data=data or {}))

    def outcome(
        self,
        identity: Identity | str,
        cell: Cell,
        data: Mapping[str, Any] | None = None,
        attempt: int | None = None,
        expectation: Expectation | None = None,
    ) -> bool:
        """Record *attempt* of a task's outcome (default: the next one) under its *expectation*, subject to finality.

        Pass the attempt explicitly to make a re-append of the same event idempotent.
        """
        return self.append(Event(OUTCOME, _key(identity), cell, data or {}, attempt, expectation))

    def attempts(self, identity: Identity | str) -> int:
        """How many outcome attempts are recorded for *identity*."""
        return len(self._attempts.get(_key(identity), []))

    def replay(self) -> Iterator[Event]:
        """Every event, in the order it was recorded."""
        yield from list(self._events)

    def latest(self) -> dict[str, Cell]:
        """Per identity with an outcome, its current cell (a decision, once there is one)."""
        return dict(self._latest)

    def decided(self) -> dict[str, Cell]:
        """Per identity, its final DECIDED cell."""
        return {key: cell for key, cell in self._latest.items() if cell.status.decided}

    def pending(self) -> list[str]:
        """Every identity seen with no decision yet, in first-seen order."""
        return [key for key in self._submitted if not (key in self._latest and self._latest[key].status.decided)]

    def by_status(self, status: Status) -> list[str]:
        """Every identity whose current status is *status*; one with no outcome yet is ``PENDING``."""
        return [key for key in self._submitted if self._latest.get(key, Cell(Status.PENDING)).status is status]
