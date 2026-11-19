"""THE PYTEST MAPPING (plan II.3, A1.1/A1.2): one phase report -> a plain test cell; a run -> test cells.

A skip is mapped by the marker that DECLARES it -- a declared resource marker is UNAVAILABLE,
``not_applicable`` is NOT_APPLICABLE -- and a skip with neither is refused by name (XFAIL-NOT-SKIP).
An xfail is REJECTED under an Expectation of REJECTED, never a status of its own.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_commons.hpc import pytest_item
from lab_commons.hpc.records import (
    STATE_KEYS,
    RetiredRecord,
    RetiredState,
    current_state,
    migrated_state,
    read_record,
    write_record,
)
from lab_commons.hpc.retired import TEST_WORDS
from lab_commons.hpc.testcells import TestCell, conclude, merged, upgraded
from lab_commons.outcomes import Cell, Conclusion, Expectation, Reason, Status
from lab_commons.temp import TEMP_PREFIX

RESOURCES = ('femm',)

#: The retired word for a handed id, read from the one place it may be spelled.
HANDED = next(word for word, reads in TEST_WORDS.items() if reads is None)


def _report(when: str, outcome: str, longrepr: object = None, marks: tuple[str, ...] = (), **extra: object) -> object:
    return SimpleNamespace(
        nodeid='t.py::a',
        when=when,
        outcome=outcome,
        duration=0.1,
        longrepr=longrepr,
        keywords=dict.fromkeys(marks, 1),
        **extra,
    )


def _cell(report: object) -> TestCell:
    plain = pytest_item.judged(report, RESOURCES)
    assert plain is not None
    return TestCell.from_plain(plain)


def test_the_item_runner_restates_exactly_the_decided_family() -> None:
    """It imports nothing, so it spells the family by token; this pins the two equal."""
    assert {status.value for status in Status if status.decided} == pytest_item.DECIDED


@pytest.mark.parametrize(
    ('report', 'word'),
    [
        (_report('call', 'passed'), 'ok'),
        (_report('call', 'failed', 'E   assert 1 == 2'), 'rejected'),
        (_report('setup', 'failed', 'E   RuntimeError: fixture'), 'error'),
        (_report('teardown', 'failed', 'E   RuntimeError: teardown'), 'error'),
        (_report('call', 'failed', 'E   Failed: Timeout >10.0s'), 'timeout'),
        (_report('call', 'skipped', 'xfail', wasxfail='issue 7'), 'rejected/expected-rejected'),
        (_report('call', 'passed', wasxfail='issue 7'), 'ok/expected-rejected'),
        (_report('call', 'failed', '[XPASS(strict)] issue 7'), 'ok/expected-rejected'),
        (_report('setup', 'skipped', ('t.py', 1, 'Skipped: no femm'), ('femm',)), 'unavailable'),
        (_report('setup', 'skipped', ('t.py', 1, 'Skipped: posix only'), ('not_applicable',)), 'not_applicable'),
    ],
)
def test_each_pytest_outcome_maps_to_its_cell(report: object, word: str) -> None:
    assert _cell(report).word() == word


def test_a_passing_setup_or_teardown_decides_nothing() -> None:
    assert pytest_item.judged(_report('setup', 'passed'), RESOURCES) is None
    assert pytest_item.judged(_report('teardown', 'passed'), RESOURCES) is None


def test_planted_control_a_skip_with_no_declaring_marker_is_refused_by_name() -> None:
    """XFAIL-NOT-SKIP: an undeclared skip is a decided miss naming the rule, never a green dot."""
    one = _cell(_report('setup', 'skipped', ('t.py', 1, 'Skipped: no vendor'), ('slow',)))
    assert one.red
    assert one.cell.reason == Reason('xfail_not_skip', one.why)
    assert one.why.startswith('XFAIL-NOT-SKIP')
    assert one.why.endswith('no vendor')


def test_an_xfail_and_an_xpass_are_judged_against_their_expectation() -> None:
    xfail = _cell(_report('call', 'skipped', 'xfail', wasxfail='issue 7'))
    xpass = _cell(_report('call', 'passed', wasxfail='issue 7'))
    assert xfail.expectation == Expectation(Status.REJECTED, 'issue 7')
    assert (xfail.met, xpass.met) == (True, False), 'an xpass is a decided miss: the expected rejection did not come'


def test_declared_resources_are_read_from_the_tested_repos_pyproject(tmp_path: Path) -> None:
    (tmp_path / 'pyproject.toml').write_text('[tool.lab_commons.tests]\nresources = ["femm", "jmag"]\n')
    assert pytest_item.declared_resources(tmp_path / 'pyproject.toml') == {'femm', 'jmag'}
    assert pytest_item.declared_resources(tmp_path / 'absent.toml') == frozenset()


def test_a_run_concludes_over_its_cells_and_a_flaky_id_makes_a_pass_inconclusive() -> None:
    ok, red = TestCell(Cell(Status.OK)), TestCell(Cell(Status.REJECTED, reason=Reason('assertion')))
    lost = TestCell(Cell(Status.INFRA, reason=Reason('lost')))
    assert conclude([ok, ok]) is Conclusion.PASS
    assert conclude([ok, lost]) is Conclusion.INCONCLUSIVE
    assert conclude([ok, red, lost]) is Conclusion.FAIL, 'a wrong answer is wrong whatever else is missing'
    assert conclude([ok], flaky=['t.py::a']) is Conclusion.INCONCLUSIVE


def test_two_processes_deciding_one_id_both_ways_make_it_flaky_and_never_overwrite() -> None:
    ok, red = TestCell(Cell(Status.OK)), TestCell(Cell(Status.REJECTED, reason=Reason('assertion')))
    lost = TestCell(Cell(Status.INFRA, reason=Reason('lost')))
    cells, flaky = merged([{'a': ok, 'b': lost}, {'a': red, 'b': ok}])
    assert (cells, flaky) == ({'a': ok, 'b': ok}, {'a'}), 'the first decision stands; a retryable is superseded'


def test_a_regenerable_run_record_of_the_retired_words_is_refused_by_name(tmp_path: Path) -> None:
    """FORMATS-MIGRATE-ONCE: a run record is never read in an old shape: the forge re-produces the sha."""
    path = write_record({'sha': 'a' * 40, 'platform': 'base', 'req': 'r', 'outcomes': {'t.py::a': 'passed'}}, tmp_path)
    with pytest.raises(RetiredRecord, match='retired word shape'):
        read_record(path)
    fresh = write_record({'sha': 'b' * 40, 'platform': 'base', 'req': 'r', 'cells': {}}, tmp_path)
    assert read_record(fresh)['cells'] == {}, 'PLANTED CONTROL: the current shape reads'


def test_the_one_shot_migration_reads_a_state_of_the_retired_words_as_cells() -> None:
    """FORMATS-MIGRATE-ONCE: the retired ``outcomes`` words migrate once, by :func:`migrated_state`."""
    state = {
        'sha': 's',
        'handed': {'w.py::t': 'win'},
        'outcomes': {'a.py::t': 'passed', 'b.py::t': 'failed', 'c.py::t': 'lost', 'w.py::t': HANDED},
        'reasons': {'b.py::t': 'boom'},
        'record': {'handed': {}, 'outcomes': {'d.py::t': 'xfailed'}, 'reasons': {}},
    }
    read = migrated_state(state)
    assert {n: TestCell.from_plain(p).word() for n, p in read['cells'].items()} == {
        'a.py::t': 'ok',
        'b.py::t': 'rejected',
        'c.py::t': 'infra',
    }, 'a handed id is no cell'
    assert TestCell.from_plain(read['cells']['b.py::t']).why == 'boom'
    assert 'outcomes' not in read
    assert 'reasons' not in read
    assert TestCell.from_plain(read['record']['cells']['d.py::t']).word() == 'rejected/expected-rejected'
    pending = upgraded('missing')
    assert pending is not None
    assert pending.status is Status.PENDING
    assert upgraded(HANDED) is None, 'a handed id is no cell'


def test_the_one_shot_migration_lands_a_file_keyed_growth_on_the_ids_in_flight() -> None:
    """Before 2026-10-30 a run's ``grown`` was keyed by FILE; the migration keys it by id.

    PLANTED CONTROL: an id key is kept as it is, and a file with nothing in flight lands nowhere.
    """
    state = {
        'current': {'items': [{'ids': ['a.py::x', 'a.py::y', 'b.py::z']}]},
        'grown': {'a.py': 2.0, 'c.py': 4.0, 'b.py::z': 8.0},
    }
    assert migrated_state(state)['grown'] == {'a.py::x': 2.0, 'a.py::y': 2.0, 'b.py::z': 8.0}


_CURRENT: dict[str, object] = {
    'sha': 'a' * 40,
    'cells': {},
    'rounds': [],
    'measured': {},
    'priced': {},
    'item_wall': {},
    'grown': {},
    'held': {},
}


@pytest.mark.parametrize('key', sorted(STATE_KEYS))
def test_a_state_lacking_a_key_every_writer_writes_is_refused_naming_its_migration(key: str) -> None:
    """FORMATS-MIGRATE-ONCE: the reader fills no default; the refusal names the key and the migration.

    PLANTED CONTROL: the same state WITH the key reads as it is, so the refusal is the key's, not the state's.
    """
    assert current_state(dict(_CURRENT)) == _CURRENT
    lacking = {k: v for k, v in _CURRENT.items() if k != key}
    with pytest.raises(RetiredState, match=rf"missing '{key}'.*migrated_state"):
        current_state(lacking)
    assert current_state(migrated_state(lacking)).keys() >= STATE_KEYS, 'the migration lands the current shape'


@pytest.mark.parametrize(
    ('retired', 'named'),
    [
        ({'outcomes': {}}, "retired 'outcomes'"),
        ({'oom': 2}, "retired 'oom'"),
        ({'rounds': [{'job_id': '1'}]}, "retired 'job_id'"),
        ({'grown': {'a.py': 2.0}}, "retired file-keyed 'grown'"),
        ({'record': {'outcomes': {}}}, "retired record 'outcomes'"),
    ],
)
def test_a_state_in_a_retired_shape_is_refused_and_migrates_once(retired: dict[str, object], named: str) -> None:
    state = {**_CURRENT, **retired}
    with pytest.raises(RetiredState, match=named):
        current_state(state)
    current_state(migrated_state(state))


def test_a_state_without_record_or_current_is_not_yet_settled_or_in_flight_and_reads() -> None:
    """``record`` and ``current`` legitimately come and go; their absence means "not yet", never old."""
    assert 'record' not in _CURRENT
    assert 'current' not in _CURRENT
    current_state(dict(_CURRENT))


def test_the_item_runner_restates_exactly_the_family_temp_prefix() -> None:
    """It imports nothing, so it spells the family's temp prefix; this pins the two equal."""
    assert pytest_item.TEMP_PREFIX == TEMP_PREFIX
