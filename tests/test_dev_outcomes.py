"""The per-test OUTCOME recorder: one word per node id in the run record's vocabulary, skip reasons kept."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lab_commons.dev.outcomes import OutcomeRecorder, read_outcomes, read_reasons


class _Report(SimpleNamespace):
    """The attributes :class:`OutcomeRecorder` reads off a pytest report."""


def _report(nodeid: str, when: str, outcome: str, **extra: object) -> _Report:
    longrepr = extra.pop('longrepr', None)
    return _Report(
        nodeid=nodeid,
        when=when,
        passed=outcome == 'passed',
        failed=outcome == 'failed',
        skipped=outcome == 'skipped',
        longrepr=longrepr,
        **extra,
    )


def test_the_recorder_speaks_the_run_record_vocabulary_and_keeps_skip_reasons(tmp_path: Path) -> None:
    recorder = OutcomeRecorder(directory=tmp_path)
    for report in (
        _report('a::passes', 'setup', 'passed'),
        _report('a::passes', 'call', 'passed'),
        _report('a::fails', 'call', 'failed'),
        _report('a::teardown_breaks', 'call', 'passed'),
        _report('a::teardown_breaks', 'teardown', 'failed'),
        _report('a::skipped', 'setup', 'skipped', longrepr=('a.py', 3, 'Skipped: needs a GPU')),
        _report('a::expected', 'call', 'skipped', wasxfail='known'),
    ):
        recorder.pytest_runtest_logreport(report)
    recorder.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()))
    assert read_outcomes(tmp_path) == {
        'a::passes': 'passed',
        'a::fails': 'failed',
        'a::teardown_breaks': 'failed',
        'a::skipped': 'skipped',
        'a::expected': 'xfailed',
    }
    assert read_reasons(tmp_path) == {'a::skipped': 'needs a GPU'}


def test_outcomes_from_several_workers_are_merged_and_the_worse_word_wins(tmp_path: Path) -> None:
    for worker, outcome in (('gw0', 'passed'), ('gw1', 'failed'), ('gw2', 'passed')):
        recorder = OutcomeRecorder(directory=tmp_path)
        recorder.pytest_runtest_logreport(_report('a::one', 'call', outcome))
        recorder.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace(workerinput={'workerid': worker})))
    assert read_outcomes(tmp_path) == {'a::one': 'failed'}


def test_no_outcomes_directory_reads_as_none_recorded(tmp_path: Path) -> None:
    assert read_outcomes(tmp_path / 'absent') == {}
    assert read_reasons(tmp_path / 'absent') == {}


def test_the_recorder_is_a_hashable_plugin(tmp_path: Path) -> None:
    """Pytest >= 8.4 keeps registered plugins in a set; a value-equality dataclass is unhashable there."""
    recorder = OutcomeRecorder(directory=tmp_path)
    assert {recorder} == {recorder}
    assert len({recorder, OutcomeRecorder(directory=tmp_path)}) == 2
