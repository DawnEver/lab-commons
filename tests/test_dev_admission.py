"""PUSH ADMISSION, the family's one rule: a lane cites any judged-or-not verdict, the trunk only a PASS.

User ruling 2026-10-04 ("INCONCLUSIVE may be pushed"): a lane or integration push cites PASS, FAIL or
INCONCLUSIVE for the SAME tree and env, and records the gap; the trunk keeps the strict bar. What is
still refused is the absence of a verdict about THIS tree: none at all, another tree, another env,
or a dirty working tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lab_commons.dev import admission

if TYPE_CHECKING:
    from pathlib import Path

HEAD = 'abc1234'
ENV = 'env0001'


def _line(result: str, *, tier: str = 'gate', tree: str = HEAD, env: str = ENV) -> str:
    return f'[verdict tree={tree} env={env} tier={tier} selector=12-paths] {result} -- detail'


def _anchor(tmp_path: Path, name: str, *lines: str) -> Path:
    path = tmp_path / name
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def test_the_parser_reads_the_last_verdict_in_either_grammar() -> None:
    text = '\n'.join([_line('FAIL'), 'noise', _line('PASS', tier='heavy')])
    parsed = admission.parse_verdict_line(text)
    assert parsed is not None
    assert (parsed.tree, parsed.env, parsed.tier, parsed.result) == (HEAD, ENV, 'heavy', 'PASS')
    stamped = 'VERDICT result=inconclusive tree=t1 env=e1 log=sha256:00@3 selector=verify'
    flat = admission.parse_verdict_line(_line('PASS') + '\n' + stamped)
    assert flat is not None
    assert (flat.tree, flat.tier, flat.result) == ('t1', None, 'INCONCLUSIVE')
    assert admission.parse_verdict_line('no verdict here') is None


@pytest.mark.parametrize('result', ['PASS', 'FAIL', 'INCONCLUSIVE'])
def test_a_lane_admits_every_result_for_this_tree_and_env(tmp_path: Path, result: str) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line(result))
    decided = admission.admit([('gate', gate)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV)
    assert decided.allowed
    assert decided.cited is not None
    assert decided.cited.result == result
    assert decided.cited.line in decided.message
    assert ('!!! INCONCLUSIVE' in decided.message) is (result == 'INCONCLUSIVE')


def test_a_cited_inconclusive_is_announced_loudly(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    decided = admission.admit([('gate', gate)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV)
    assert '!!! INCONCLUSIVE' in decided.message
    assert 'REFUSED' not in decided.message


def test_the_trunk_admits_only_a_pass_of_its_own_tier(tmp_path: Path) -> None:
    for result, allowed in (('PASS', True), ('FAIL', False), ('INCONCLUSIVE', False)):
        heavy = _anchor(tmp_path, f'heavy-{result}.verdict', _line(result, tier='heavy'))
        decided = admission.admit(
            [('heavy', heavy)], trunk=True, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
        )
        assert decided.allowed is allowed, result
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    refused = admission.admit([('gate', gate)], trunk=True, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV)
    assert not refused.allowed
    assert 'REFUSED' in refused.message


@pytest.mark.parametrize(
    ('line', 'tree_state', 'why'),
    [
        (None, 'clean', 'no verdict'),
        (_line('PASS', tree='fff0000'), 'clean', 'tree='),
        (_line('PASS', env='other'), 'clean', 'env='),
        (_line('PASS'), 'dirty', 'dirty'),
    ],
)
def test_a_lane_still_refuses_what_is_not_a_verdict_about_this_tree(
    tmp_path: Path,
    line: str | None,
    tree_state: str,
    why: str,
) -> None:
    clean = tree_state == 'clean'
    anchor = _anchor(tmp_path, 'gate.verdict', line) if line else tmp_path / 'absent.verdict'
    decided = admission.admit([('gate', anchor)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=clean, env=ENV)
    assert not decided.allowed
    assert why in decided.message


def test_a_lane_prefers_the_strongest_result_across_tiers(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    heavy = _anchor(tmp_path, 'heavy.verdict', _line('FAIL', tier='heavy'))
    decided = admission.admit(
        [('gate', gate), ('heavy', heavy)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
    )
    assert decided.cited is not None
    assert decided.cited.tier == 'heavy'


def test_a_full_head_sha_matches_a_short_stamp(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    decided = admission.admit(
        [('gate', gate)], trunk=False, trunk_tiers=('heavy',), head=HEAD + 'd' * 33, clean=True, env=ENV
    )
    assert decided.allowed


@pytest.mark.parametrize('result', ['PASS', 'FAIL', 'INCONCLUSIVE'])
def test_a_fresh_lane_log_decides_by_the_same_table(tmp_path: Path, result: str) -> None:
    log = _anchor(tmp_path, 'gate.log', 'ran', _line(result))
    assert admission.decide_fresh(log, trunk=False).allowed
    assert admission.decide_fresh(log, trunk=True).allowed is (result == 'PASS')


def test_a_fresh_run_with_no_log_or_no_verdict_is_refused(tmp_path: Path) -> None:
    assert not admission.decide_fresh(tmp_path / 'missing.log', trunk=False).allowed
    assert not admission.decide_fresh(_anchor(tmp_path, 'g.log', 'died'), trunk=False).allowed


def test_the_gap_record_carries_the_line_the_failures_and_the_never_ran_count(tmp_path: Path) -> None:
    log_text = '\n'.join(
        [
            'FAILED tests/test_a.py::test_one - boom',
            'ERROR tests/test_b.py::test_two',
            '7 of 40 asked NOT RUN',
            _line('INCONCLUSIVE'),
        ]
    )
    cited = admission.parse_verdict_line(log_text)
    assert cited is not None
    gap = admission.record_gap(tmp_path / 'gaps' / 'gap.md', cited, log_text)
    body = gap.read_text(encoding='utf-8')
    assert cited.line in body
    assert 'tests/test_a.py::test_one' in body
    assert 'tests/test_b.py::test_two' in body
    assert 'never ran: 7' in body
    again = admission.record_gap(gap, cited, _line('INCONCLUSIVE'))
    assert 'never ran: unknown' in again.read_text(encoding='utf-8'), 'a refresh REWRITES the record'


def test_a_pass_writes_no_gap_record(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    gap = tmp_path / 'gap.md'
    decided = admission.admit(
        [('gate', gate)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV, gap=gap
    )
    assert decided.allowed
    assert not gap.exists()
    inconclusive = _anchor(tmp_path, 'gate2.verdict', _line('INCONCLUSIVE'))
    admission.admit(
        [('gate', inconclusive)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV, gap=gap
    )
    assert gap.is_file()
    assert (
        str(gap)
        in admission.admit(
            [('gate', inconclusive)], trunk=False, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV, gap=gap
        ).message
    )


def test_the_cli_routes_the_trunk_ref_to_the_strict_bar(tmp_path: Path, monkeypatch, capsys) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    monkeypatch.setattr(admission, '_tree_state', lambda _root: (HEAD, True))
    monkeypatch.setattr(admission, '_current_env', lambda: ENV)
    base = [
        '--root',
        str(tmp_path),
        '--trunk-ref',
        'refs/heads/main',
        '--trunk-tier',
        'heavy',
        '--anchor',
        f'gate={gate}',
    ]
    assert admission.main([*base, '--remote-ref', 'refs/heads/feat/x']) == 0
    assert '!!! INCONCLUSIVE' in capsys.readouterr().out
    assert admission.main([*base, '--remote-ref', 'refs/heads/main']) == 1
    assert admission.main([*base, '--remote-ref', 'refs/heads/main', '--fresh-log', str(gate)]) == 1
    assert admission.main([*base, '--remote-ref', 'refs/heads/feat/x', '--fresh-log', str(gate)]) == 0
