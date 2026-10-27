"""A red or unanswered id carries its reason through stream, fold and record -- a red triages without a re-run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_commons.compute.measured import Measured
from lab_commons.hpc import pytest_item
from lab_commons.hpc.pytest_item import read_stream
from lab_commons.hpc.records import fold
from lab_commons.hpc.testcells import TestCell


def _report(when: str, outcome: str, longrepr: object) -> SimpleNamespace:
    return SimpleNamespace(nodeid='t.py::a', when=when, outcome=outcome, duration=0.1, longrepr=longrepr, keywords={})


def _words(cells: dict[str, dict[str, object]]) -> dict[str, str]:
    return {node: TestCell.from_plain(plain).word() for node, plain in cells.items()}


def test_without_a_crash_the_last_non_empty_line_is_the_reason() -> None:
    line = pytest_item.entry_of(_report('setup', 'failed', 'Traceback\n  x\nE   ' + 'v' * 400 + '\n\n'))
    assert line is not None
    assert line['status'] == 'error'
    assert line['reason'] == {'kind': 'setup', 'text': ('E   ' + 'v' * 400)[:300]}


def test_a_passing_phase_carries_no_reason() -> None:
    line = pytest_item.entry_of(_report('call', 'passed', None))
    assert line is not None
    assert 'reason' not in line


def test_a_test_that_redirects_the_stream_variable_still_reports_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plugin's stream is the RUN's, bound when the plugin loads; a test's environment cannot redirect it.

    THE CLUSTER INCIDENT, 2026-10-12: the two tests above once set ``LAB_CI_STREAM`` themselves. The
    plugin read that variable on every append, so their own results went to THEIR stream and the run
    recorded both unreported -- an inconclusive verdict from two passing tests.
    """
    inner = tmp_path / 'test_inner.py'
    inner.write_text(
        'def test_redirects(monkeypatch, tmp_path):\n'
        f"    monkeypatch.setenv('{pytest_item.STREAM_ENV}', str(tmp_path / 'elsewhere.jsonl'))\n",
        encoding='utf-8',
    )
    monkeypatch.setenv('PYTHONPATH', str(Path(pytest_item.__file__).parent))
    monkeypatch.chdir(tmp_path)
    done = pytest_item.run({'ids': ['test_inner.py::test_redirects'], 'stream': str(tmp_path / 'run.jsonl'), 'item': 0})
    assert _words(done['cells']) == {'test_inner.py::test_redirects': 'ok'}, done['tail']


def test_the_worst_phase_wins_with_its_reason() -> None:
    """A decided miss beats no answer, which beats an answer as declared; the first of equals stays."""
    error = '"status": "error", "reason": {"kind": "teardown", "text": "teardown boom"}'
    text = (
        f'{{"id": "t.py::a", {error}, "s": 0}}\n'
        '{"id": "t.py::a", "status": "rejected", "reason": {"kind": "assertion", "text": "assert"}, "s": 0}\n'
        '{"id": "t.py::b", "status": "ok", "s": 0}\n'
        '{"id": "t.py::c", "status": "rejected", "reason": {"kind": "assertion", "text": "first"}, "s": 0}\n'
        f'{{"id": "t.py::c", {error}, "s": 0}}\n'
    )
    cells = read_stream(text, ['t.py::a', 't.py::b', 't.py::c'])['cells']
    whys = {node: TestCell.from_plain(plain).why for node, plain in cells.items()}
    assert whys == {'t.py::a': 'assert', 't.py::b': '', 't.py::c': 'first'}


def test_fold_keeps_each_reason_in_its_cell() -> None:
    items = [{'ids': ['t.py::a', 't.py::b']}]
    streams = {
        0: '{"id": "t.py::a", "status": "rejected", "reason": {"kind": "assertion", "text": "boom"}, "s": 0}\n'
        '{"id": "t.py::b", "status": "ok", "s": 0}\n{"done": 1, "wall": 1, "peak_mb": null}'
    }
    cells: dict[str, dict[str, object]] = {}
    assert fold(items, streams, cells, Measured.of({}), shards=[(0, 1)], walls=[60.0])[:2] == ([], False)
    assert TestCell.from_plain(cells['t.py::a']).why == 'boom'


def test_an_item_killed_by_a_signal_retries_its_unreported_ids() -> None:
    items = [{'ids': ['t.py::a', 't.py::b', 't.py::c']}]
    streams = {0: '{"id": "t.py::a", "status": "ok", "s": 0}\n{"done": -9, "wall": 14.7, "peak_mb": 1112.9}\n'}
    cells: dict[str, dict[str, object]] = {}
    pending, killed, _ = fold(items, streams, cells, Measured.of({}), shards=[(0, 1)], walls=[60.0])
    assert killed
    assert _words(cells) == {'t.py::a': 'ok'}, 'an OOM-killed item stamped its unreported ids PENDING'
    assert sorted(node for part in pending for node in part) == ['t.py::b', 't.py::c']


def test_an_item_that_closed_cleanly_still_stamps_unreported_ids_pending() -> None:
    items = [{'ids': ['t.py::a', 't.py::b']}]
    streams = {0: '{"id": "t.py::a", "status": "ok", "s": 0}\n{"done": 0, "wall": 1.0}\n'}
    cells: dict[str, dict[str, object]] = {}
    assert fold(items, streams, cells, Measured.of({}), shards=[(0, 1)], walls=[60.0])[:2] == ([], False)
    assert _words(cells) == {'t.py::a': 'ok', 't.py::b': 'pending'}
    assert TestCell.from_plain(cells['t.py::b']).why == 'the item exited 0 without reporting it'
