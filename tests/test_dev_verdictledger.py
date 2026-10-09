"""The verdict LEDGER: one append-only record of promoted verdicts, keyed by (tree, env, test).

Every arm drives the real module over a real file. The two refusals that make it a single source of
truth rather than a cache of guesses are planted: an INCONCLUSIVE is never written, and a key that
has seen both PASS and FAIL (flaky) is never served.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_commons.dev import verdictledger
from lab_commons.dev.verdictledger import (
    Entry,
    LedgerRefusal,
    OutcomeRecorder,
    ledger_path,
    reach,
    read_outcomes,
    record,
    run_test_id,
    served,
)

GIT = shutil.which('git') or 'git'


def _entry(result: str = 'PASS', *, test: str = 'tests/a.py::t', tree: str = 'T', env: str = 'E') -> Entry:
    return Entry(tree=tree, env=env, test=test, result=result, tier='verify', commit='c' * 40, log='x.log')


def test_a_recorded_pass_is_served_for_its_key_only(tmp_path: Path) -> None:
    path = tmp_path / 'ledger.jsonl'
    record(path, [_entry()])
    found = served(path, tree='T', env='E', test='tests/a.py::t')
    assert found is not None
    assert replace(found, at='') == _entry()
    assert served(path, tree='T2', env='E', test='tests/a.py::t') is None, 'another tree was served'
    assert served(path, tree='T', env='E2', test='tests/a.py::t') is None, 'another env was served'


def test_a_fail_is_cached_like_a_pass(tmp_path: Path) -> None:
    """Deterministic is the default: a FAIL on a key is the answer for that key."""
    path = tmp_path / 'ledger.jsonl'
    record(path, [_entry('FAIL')])
    found = served(path, tree='T', env='E', test='tests/a.py::t')
    assert found is not None
    assert found.result == 'FAIL'


def test_an_inconclusive_is_refused_at_the_writer(tmp_path: Path) -> None:
    """PLANTED: nobody-knows is not a fact about the tree, so it never enters the record."""
    with pytest.raises(LedgerRefusal, match='INCONCLUSIVE'):
        record(tmp_path / 'ledger.jsonl', [_entry('INCONCLUSIVE')])
    assert not (tmp_path / 'ledger.jsonl').exists()


def test_a_key_seen_both_ways_is_flaky_and_never_served(tmp_path: Path) -> None:
    path = tmp_path / 'ledger.jsonl'
    record(path, [_entry('PASS')])
    record(path, [_entry('FAIL')])
    assert served(path, tree='T', env='E', test='tests/a.py::t') is None


def test_an_absent_ledger_serves_nothing(tmp_path: Path) -> None:
    assert served(tmp_path / 'none.jsonl', tree='T', env='E', test='x') is None


def test_the_run_level_id_names_the_selection() -> None:
    assert run_test_id('verify tests/a.py') == 'run:verify tests/a.py'


def test_reach_is_the_whole_tree_until_the_follow_up_lands() -> None:
    """THE NAMED SEAM: per-test reach hashing is a follow-up; today every test reaches the whole tree."""
    assert reach('tests/a.py::t', tree='T') == 'T'
    assert verdictledger.REACH_IS_WHOLE_TREE is True


def test_the_ledger_lives_in_the_main_checkout_even_from_a_worktree(tmp_path: Path) -> None:
    """ONE file per repository: a lane resolves the main checkout's path, never its own copy."""
    main = tmp_path / 'main'
    main.mkdir()

    def git(*args: str, cwd: Path = main) -> None:
        subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True, encoding='utf-8')

    git('init', '-q')
    git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'root')
    lane = main / '.claude' / 'worktrees' / 'lane'
    git('worktree', 'add', '-q', '--detach', str(lane), 'HEAD')
    expected = main.resolve() / verdictledger.LEDGER_REL
    assert ledger_path(lane).resolve() == expected
    assert ledger_path(main).resolve() == expected


# -- the runner's half: which node ids passed or failed in THIS run -------------------------------


class _Report(SimpleNamespace):
    """The attributes :class:`OutcomeRecorder` reads off a pytest report."""


def _report(nodeid: str, when: str, outcome: str, **keywords: object) -> _Report:
    return _Report(nodeid=nodeid, when=when, passed=outcome == 'passed', failed=outcome == 'failed', keywords=keywords)


def test_the_recorder_keeps_pass_and_fail_and_drops_skips_and_live_tests(tmp_path: Path) -> None:
    """A skip is not a result; a live-engine test's input is outside the tree, so it is never recorded."""
    recorder = OutcomeRecorder(directory=tmp_path)
    for report in (
        _report('a::passes', 'setup', 'passed'),
        _report('a::passes', 'call', 'passed'),
        _report('a::fails', 'call', 'failed'),
        _report('a::teardown_breaks', 'call', 'passed'),
        _report('a::teardown_breaks', 'teardown', 'failed'),
        _report('a::skipped', 'setup', 'skipped'),
        _report('a::vendor', 'call', 'passed', live=True),
    ):
        recorder.pytest_runtest_logreport(report)
    recorder.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()))
    assert read_outcomes(tmp_path) == {'a::passes': 'PASS', 'a::fails': 'FAIL', 'a::teardown_breaks': 'FAIL'}


def test_outcomes_from_several_workers_are_merged(tmp_path: Path) -> None:
    for worker, nodeid in (('gw0', 'a::one'), ('gw1', 'a::two')):
        recorder = OutcomeRecorder(directory=tmp_path)
        recorder.pytest_runtest_logreport(_report(nodeid, 'call', 'passed'))
        recorder.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace(workerinput={'workerid': worker})))
    assert read_outcomes(tmp_path) == {'a::one': 'PASS', 'a::two': 'PASS'}


def test_no_outcomes_directory_reads_as_none_recorded(tmp_path: Path) -> None:
    assert read_outcomes(tmp_path / 'absent') == {}


def test_the_recorder_is_a_hashable_plugin(tmp_path: Path) -> None:
    """Pytest >= 8.4 keeps registered plugins in a set; a value-equality dataclass is unhashable there."""
    recorder = OutcomeRecorder(directory=tmp_path)
    assert {recorder} == {recorder}
    assert OutcomeRecorder(directory=tmp_path) is not recorder
    assert len({recorder, OutcomeRecorder(directory=tmp_path)}) == 2
