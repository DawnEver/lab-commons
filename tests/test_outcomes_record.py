"""A0.5: Record -- append + fsync, idempotence, DECIDED finality, views, replay; schema upgrade on read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import lab_commons.outcomes.record as record_module
from lab_commons.outcomes import (
    OUTCOME,
    SUBMITTED,
    Cell,
    Event,
    FinalityError,
    Identity,
    Reason,
    Record,
    Status,
    UnknownSchema,
)

A = Identity.of({'design': 'a'})
B = Identity.of({'design': 'b'})
OK = Cell(Status.OK, values={'torque': 1.0})
REJ = Cell(Status.REJECTED, reason=Reason('infeasible', 'g1 > 0'))
OOM = Cell(Status.OUT_OF_MEMORY, reason=Reason('out_of_memory'))


def test_every_append_is_flushed_and_fsynced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(record_module.os, 'fsync', lambda fd: (synced.append(fd), real(fd))[1])
    record = Record(tmp_path / 'r.jsonl')
    record.submit(A)
    record.outcome(A, OK)
    assert len(synced) == 2
    assert len((tmp_path / 'r.jsonl').read_bytes().splitlines()) == 2


def test_idempotence_a_repeat_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / 'r.jsonl'
    record = Record(path)
    assert record.submit(A) is True
    assert record.submit(A) is False
    assert record.outcome(A, OK) is True
    assert record.outcome(A, OK) is False
    assert Record(path).outcome(A, OK) is False, 'a reopened record knows what it holds'
    assert len(path.read_bytes().splitlines()) == 2


def test_planted_control_a_conflicting_second_decided_is_refused(tmp_path: Path) -> None:
    path = tmp_path / 'r.jsonl'
    record = Record(path)
    record.outcome(A, REJ)
    with pytest.raises(FinalityError, match='final'):
        record.outcome(A, OK)
    with pytest.raises(FinalityError, match='final'):
        Record(path).outcome(A, OOM)
    assert record.latest()[A.key] == REJ


def test_retryable_is_superseded(tmp_path: Path) -> None:
    record = Record(tmp_path / 'r.jsonl')
    record.outcome(A, OOM)
    record.outcome(A, Cell(Status.TIMEOUT, reason=Reason('wall')))
    record.outcome(A, OOM)
    assert record.latest()[A.key] == OOM, 'a retryable that recurs after another is recorded again'
    record.outcome(A, OK)
    assert record.decided() == {A.key: OK}


def test_views(tmp_path: Path) -> None:
    record = Record(tmp_path / 'r.jsonl')
    record.submit(A)
    record.submit(B)
    record.outcome(B, OOM)
    assert record.pending() == [A.key, B.key]
    assert record.by_status(Status.PENDING) == [A.key]
    assert record.by_status(Status.OUT_OF_MEMORY) == [B.key]
    record.outcome(A, OK)
    assert record.pending() == [B.key]
    assert record.decided() == {A.key: OK}
    assert record.latest() == {A.key: OK, B.key: OOM}


def test_replay_yields_events_in_order_including_caller_kinds(tmp_path: Path) -> None:
    path = tmp_path / 'r.jsonl'
    record = Record(path)
    record.submit(A, {'host': 'n1'})
    record.append(Event('started', A.key, data={'pid': 7}))
    record.outcome(A, OK)
    expected = [
        Event(SUBMITTED, A.key, data={'host': 'n1'}),
        Event('started', A.key, data={'pid': 7}),
        Event(OUTCOME, A.key, OK),
    ]
    assert list(record.replay()) == expected
    assert list(Record(path).replay()) == expected


def test_a_torn_tail_is_cut_before_the_next_append(tmp_path: Path) -> None:
    """Otherwise the next line would be glued onto the torn bytes and become a torn MIDDLE line."""
    path = tmp_path / 'r.jsonl'
    Record(path).outcome(A, OK)
    with path.open('ab') as handle:
        handle.write(b'{"schema":1,"kin')
    record = Record(path)
    record.outcome(B, OK)
    assert list(Record(path).replay()) == [Event(OUTCOME, A.key, OK), Event(OUTCOME, B.key, OK)]


def test_an_older_schema_is_upgraded_on_read_and_an_unknown_one_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORMATS-UPGRADE-ON-READ: the reader consults ``_UPGRADES`` before it refuses."""
    plain = {'schema': 0, 'kind': SUBMITTED, 'id': A.key}
    with pytest.raises(UnknownSchema):
        Event.from_plain(plain)
    upgrades = {0: lambda raw: {'schema': 1, 'kind': raw['kind'], 'key': raw['id']}}
    monkeypatch.setattr(record_module, '_UPGRADES', upgrades)
    assert Event.from_plain(plain) == Event(SUBMITTED, A.key)
    with pytest.raises(UnknownSchema):
        Event.from_plain({'schema': 99, 'kind': SUBMITTED, 'key': A.key})


def test_an_outcome_needs_a_cell_and_nothing_else_carries_one() -> None:
    with pytest.raises(ValueError, match='cell'):
        Event(OUTCOME, A.key)
    with pytest.raises(ValueError, match='cell'):
        Event(SUBMITTED, A.key, OK)
