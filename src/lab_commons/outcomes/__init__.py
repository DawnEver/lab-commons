"""One task-outcome vocabulary: what happened to each identified task.

Standard library only, and it imports no other ``lab_commons`` subpackage, so every engine can depend
on it without inheriting anything else. Importing ``lab_commons`` does not import it.

- :mod:`~lab_commons.outcomes.status` -- ``Family`` (DECIDED / RETRYABLE), ``Status``, ``Evidence``.
- :mod:`~lab_commons.outcomes.cell` -- ``Reason``, ``Cost``, ``Cell`` and its invariants.
- :mod:`~lab_commons.outcomes.identity` -- ``Identity`` (canonical encoding + SHA-256), ``INACTIVE``.
- :mod:`~lab_commons.outcomes.expectation` -- ``Expectation``, part of a task's declaration.
- :mod:`~lab_commons.outcomes.jsonl` -- the append-only JSONL reader (torn last line tolerated).
- :mod:`~lab_commons.outcomes.record` -- ``Record``: durable events, DECIDED finality, views, replay.
"""

from __future__ import annotations

from lab_commons.outcomes.cell import Cell, Cost, Reason
from lab_commons.outcomes.expectation import Expectation
from lab_commons.outcomes.identity import INACTIVE, Identity
from lab_commons.outcomes.jsonl import JsonlRead, TornRecord, read_jsonl
from lab_commons.outcomes.record import (
    OUTCOME,
    SCHEMA_VERSION,
    SUBMITTED,
    Event,
    FinalityError,
    Record,
    UnknownSchema,
)
from lab_commons.outcomes.status import Evidence, Family, Status

__all__ = [
    'INACTIVE',
    'OUTCOME',
    'SCHEMA_VERSION',
    'SUBMITTED',
    'Cell',
    'Cost',
    'Event',
    'Evidence',
    'Expectation',
    'Family',
    'FinalityError',
    'Identity',
    'JsonlRead',
    'Reason',
    'Record',
    'Status',
    'TornRecord',
    'UnknownSchema',
    'read_jsonl',
]
