"""A failed/error id carries its reason through stream, fold and record -- a red triages without a re-run."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_commons.hpc import pytest_item
from lab_commons.hpc.measured import Measured
from lab_commons.hpc.pytest_item import read_stream
from lab_commons.hpc.records import fold


def _report(when: str, outcome: str, longrepr: object) -> SimpleNamespace:
    return SimpleNamespace(nodeid='t.py::a', when=when, outcome=outcome, duration=0.1, longrepr=longrepr)


def _logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: SimpleNamespace) -> dict:
    stream = tmp_path / 's.jsonl'
    monkeypatch.setenv(pytest_item.STREAM_ENV, str(stream))
    pytest_item.pytest_runtest_logreport(report)
    return json.loads(stream.read_text(encoding='utf-8'))


def test_without_a_crash_the_last_non_empty_line_is_the_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    line = _logged(tmp_path, monkeypatch, _report('setup', 'failed', 'Traceback\n  x\nE   ' + 'v' * 400 + '\n\n'))
    assert line['outcome'] == 'error'
    assert line['why'] == ('E   ' + 'v' * 400)[:300]


def test_a_passing_phase_carries_no_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert 'why' not in _logged(tmp_path, monkeypatch, _report('call', 'passed', None))


def test_the_worst_phase_reason_wins_with_its_outcome() -> None:
    text = (
        '{"id": "t.py::a", "outcome": "error", "s": 0, "why": "teardown boom"}\n'
        '{"id": "t.py::a", "outcome": "failed", "s": 0, "why": "assert"}\n'
        '{"id": "t.py::b", "outcome": "passed", "s": 0}\n'
        '{"id": "t.py::c", "outcome": "failed", "s": 0, "why": "first"}\n'
        '{"id": "t.py::c", "outcome": "error", "s": 0, "why": "later teardown"}\n'
    )
    assert read_stream(text, ['t.py::a', 't.py::b', 't.py::c'])['whys'] == {'t.py::a': 'assert', 't.py::c': 'first'}


def test_fold_accumulates_reasons() -> None:
    items = [{'ids': ['t.py::a', 't.py::b']}]
    streams = {
        0: '{"id": "t.py::a", "outcome": "failed", "s": 0, "why": "boom"}\n'
        '{"id": "t.py::b", "outcome": "passed", "s": 0}\n{"done": 1, "wall": 1, "peak_mb": null}'
    }
    outcomes: dict[str, str] = {}
    reasons: dict[str, str] = {}
    assert fold(items, streams, outcomes, Measured.of({}), reasons) == ([], False)
    assert reasons == {'t.py::a': 'boom'}


def test_an_item_killed_by_a_signal_retries_its_unreported_ids() -> None:
    items = [{'ids': ['t.py::a', 't.py::b', 't.py::c']}]
    streams = {0: '{"id": "t.py::a", "outcome": "passed", "s": 0}\n{"done": -9, "wall": 14.7, "peak_mb": 1112.9}\n'}
    outcomes: dict[str, str] = {}
    pending, killed = fold(items, streams, outcomes, Measured.of({}), {})
    assert killed
    assert outcomes == {'t.py::a': 'passed'}, 'an OOM-killed item stamped its unreported ids missing'
    assert sorted(node for part in pending for node in part) == ['t.py::b', 't.py::c']


def test_an_item_that_closed_cleanly_still_stamps_unreported_ids_missing() -> None:
    items = [{'ids': ['t.py::a', 't.py::b']}]
    streams = {0: '{"id": "t.py::a", "outcome": "passed", "s": 0}\n{"done": 0, "wall": 1.0}\n'}
    outcomes: dict[str, str] = {}
    assert fold(items, streams, outcomes, Measured.of({}), {}) == ([], False)
    assert outcomes == {'t.py::a': 'passed', 't.py::b': 'missing'}
