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
from lab_commons.hpc.records import RetiredRecord, read_record, upgraded_state, write_record
from lab_commons.hpc.retired import TEST_WORDS
from lab_commons.hpc.testcells import TestCell, conclude, merged, upgraded
from lab_commons.outcomes import Cell, Conclusion, Expectation, Reason, Status

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
    """No upgrade-on-read for a run record: the forge re-produces the sha."""
    path = write_record({'sha': 'a' * 40, 'platform': 'base', 'req': 'r', 'outcomes': {'t.py::a': 'passed'}}, tmp_path)
    with pytest.raises(RetiredRecord, match='retired word shape'):
        read_record(path)
    fresh = write_record({'sha': 'b' * 40, 'platform': 'base', 'req': 'r', 'cells': {}}, tmp_path)
    assert read_record(fresh)['cells'] == {}, 'PLANTED CONTROL: the current shape reads'


def test_a_live_run_state_of_the_retired_words_is_upgraded_on_read() -> None:
    """A run's state.json is LIVE (a run in flight crosses the change): FORMATS-UPGRADE-ON-READ."""
    state = {
        'sha': 's',
        'handed': {'w.py::t': 'win'},
        'outcomes': {'a.py::t': 'passed', 'b.py::t': 'failed', 'c.py::t': 'lost', 'w.py::t': HANDED},
        'reasons': {'b.py::t': 'boom'},
        'record': {'handed': {}, 'outcomes': {'d.py::t': 'xfailed'}, 'reasons': {}},
    }
    read = upgraded_state(state)
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


def test_a_live_run_states_file_keyed_growth_is_read_onto_the_ids_in_flight() -> None:
    """Before 2026-10-30 a run's ``grown`` was keyed by FILE; a run in flight across the change keys it by id.

    PLANTED CONTROL: an id key is kept as it is, and a file with nothing in flight lands nowhere.
    """
    state = {
        'current': {'items': [{'ids': ['a.py::x', 'a.py::y', 'b.py::z']}]},
        'grown': {'a.py': 2.0, 'c.py': 4.0, 'b.py::z': 8.0},
    }
    assert upgraded_state(state)['grown'] == {'a.py::x': 2.0, 'a.py::y': 2.0, 'b.py::z': 8.0}
