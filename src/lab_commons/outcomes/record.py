"""An append-only JSONL record of what happened to each identified task.

One event per line: ``submitted``, ``outcome`` (carrying a :class:`Cell`), or a caller's own kind.
Each append is flushed and fsynced before :meth:`Record.append` returns, so an event that returned is
on disk. Idempotence, so a retried writer does not double-count: an outcome whose cell equals the
identity's current cell, or any other event equal to one already recorded, writes nothing.

FINALITY: per identity the first DECIDED outcome is final. A later outcome that differs from it --
another decision or a RETRYABLE one -- is refused with :class:`FinalityError`; a RETRYABLE outcome is
superseded by whatever comes next.

ONE WRITER PER FILE. The views are built from the file at open and from this object's own appends; a
second writer's appends are not seen until the record is reopened.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.outcomes.cell import Cell
from lab_commons.outcomes.identity import Identity
from lab_commons.outcomes.jsonl import read_jsonl
from lab_commons.outcomes.status import Status

__all__ = ['OUTCOME', 'SCHEMA_VERSION', 'SUBMITTED', 'Event', 'FinalityError', 'Record', 'UnknownSchema']

#: The event-line format version, written into every line.
SCHEMA_VERSION: Final = 1

#: Each older version this build reads, and the step taking it one version on (FORMATS-UPGRADE-ON-READ).
_UPGRADES: Final[dict[int, Any]] = {}

#: The two kinds this module gives meaning to; any other kind is a caller's and is only recorded.
SUBMITTED: Final = 'submitted'
OUTCOME: Final = 'outcome'


class FinalityError(ValueError):
    """An outcome conflicts with the identity's first DECIDED outcome, which is final."""


class UnknownSchema(ValueError):
    """An event line names a schema this build neither writes nor can upgrade."""


@dataclass(frozen=True)
class Event:
    """One line: its *kind*, the identity *key*, the *cell* of an outcome, and caller *data*."""

    kind: str
    key: str
    cell: Cell | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """An outcome carries a cell, and nothing else does."""
        if (self.kind == OUTCOME) != (self.cell is not None):
            msg = f'a {self.kind!r} event {"needs" if self.kind == OUTCOME else "carries no"} cell'
            raise ValueError(msg)

    def to_plain(self) -> dict[str, Any]:
        """The JSON-ready line, schema first."""
        plain: dict[str, Any] = {'schema': SCHEMA_VERSION, 'kind': self.kind, 'key': self.key}
        if self.cell is not None:
            plain['cell'] = self.cell.to_plain()
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
        return cls(raw['kind'], raw['key'], None if cell is None else Cell.from_plain(cell), raw.get('data', {}))


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
        for event in self._events:
            self._apply(event)

    def _apply(self, event: Event) -> None:
        self._submitted.setdefault(event.key)
        if event.cell is not None:
            self._latest[event.key] = event.cell

    def _admit(self, event: Event) -> bool:
        """False for a repeat; raise for an outcome conflicting with a final decision."""
        if event.cell is None:
            return event not in self._events
        current = self._latest.get(event.key)
        if current == event.cell:
            return False
        if current is not None and current.status.decided:
            msg = f'{event.key}: {current.status.name} is final; refusing {event.cell.status.name}'
            raise FinalityError(msg)
        return True

    def append(self, event: Event) -> bool:
        """Write *event* durably; False when an equal event is already recorded (nothing written)."""
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

    def outcome(self, identity: Identity | str, cell: Cell, data: Mapping[str, Any] | None = None) -> bool:
        """Record a task's outcome, subject to finality."""
        return self.append(Event(OUTCOME, _key(identity), cell, data or {}))

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
