"""PUSH ADMISSION, the family's one rule: a lane cites any judged-or-not verdict, the trunk only a PASS.

User ruling 2026-10-04 ("INCONCLUSIVE may be pushed"): a lane or integration push cites PASS, FAIL or
INCONCLUSIVE for the SAME tree and env, and records the gap; the trunk keeps the strict bar. What is
still refused is the absence of a verdict about THIS tree: none at all, another tree, another env,
or a dirty working tree.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from lab_commons.dev import admission

if TYPE_CHECKING:
    from pathlib import Path

_GIT = shutil.which('git') or 'git'
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
    decided = admission.admit(
        [('gate', gate)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
    )
    assert decided.allowed
    assert decided.cited is not None
    assert decided.cited.result == result
    assert decided.cited.line in decided.message
    assert ('!!! INCONCLUSIVE' in decided.message) is (result == 'INCONCLUSIVE')


def test_a_cited_inconclusive_is_announced_loudly(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    decided = admission.admit(
        [('gate', gate)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
    )
    assert '!!! INCONCLUSIVE' in decided.message
    assert 'REFUSED' not in decided.message


def test_the_trunk_admits_only_a_pass_of_its_own_tier(tmp_path: Path) -> None:
    for result, allowed in (('PASS', True), ('FAIL', False), ('INCONCLUSIVE', False)):
        heavy = _anchor(tmp_path, f'heavy-{result}.verdict', _line(result, tier='heavy'))
        decided = admission.admit(
            [('heavy', heavy)], destination=admission.TRUNK, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
        )
        assert decided.allowed is allowed, result
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    refused = admission.admit(
        [('gate', gate)], destination=admission.TRUNK, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
    )
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
    decided = admission.admit(
        [('gate', anchor)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD, clean=clean, env=ENV
    )
    assert not decided.allowed
    assert why in decided.message


def test_a_lane_prefers_the_strongest_result_across_tiers(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    heavy = _anchor(tmp_path, 'heavy.verdict', _line('FAIL', tier='heavy'))
    decided = admission.admit(
        [('gate', gate), ('heavy', heavy)],
        destination=admission.LANE,
        trunk_tiers=('heavy',),
        head=HEAD,
        clean=True,
        env=ENV,
    )
    assert decided.cited is not None
    assert decided.cited.tier == 'heavy'


def test_a_full_head_sha_matches_a_short_stamp(tmp_path: Path) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    decided = admission.admit(
        [('gate', gate)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD + 'd' * 33, clean=True, env=ENV
    )
    assert decided.allowed


@pytest.mark.parametrize('result', ['PASS', 'FAIL', 'INCONCLUSIVE'])
def test_a_fresh_lane_log_decides_by_the_same_table(tmp_path: Path, result: str) -> None:
    log = _anchor(tmp_path, 'gate.log', 'ran', _line(result))
    assert admission.decide_fresh(log, destination=admission.LANE).allowed
    assert admission.decide_fresh(log, destination=admission.TRUNK).allowed is (result == 'PASS')


def test_a_fresh_run_with_no_log_or_no_verdict_is_refused(tmp_path: Path) -> None:
    assert not admission.decide_fresh(tmp_path / 'missing.log', destination=admission.LANE).allowed
    assert not admission.decide_fresh(_anchor(tmp_path, 'g.log', 'died'), destination=admission.LANE).allowed


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
        [('gate', gate)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV, gap=gap
    )
    assert decided.allowed
    assert not gap.exists()
    inconclusive = _anchor(tmp_path, 'gate2.verdict', _line('INCONCLUSIVE'))
    admission.admit(
        [('gate', inconclusive)],
        destination=admission.LANE,
        trunk_tiers=('heavy',),
        head=HEAD,
        clean=True,
        env=ENV,
        gap=gap,
    )
    assert gap.is_file()
    assert (
        str(gap)
        in admission.admit(
            [('gate', inconclusive)],
            destination=admission.LANE,
            trunk_tiers=('heavy',),
            head=HEAD,
            clean=True,
            env=ENV,
            gap=gap,
        ).message
    )


def test_the_cli_routes_the_trunk_ref_to_the_strict_bar(tmp_path: Path, monkeypatch, capsys) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    _no_merges(monkeypatch)
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


@pytest.mark.parametrize(
    'log_lines',
    [
        ['[verdict tree=abc1234 env=env0001 tier=gate selector=never-selected] INCONCLUSIVE -- cpu held'],
        ['the cpu was held by another run and this one never started', _line('INCONCLUSIVE')],
        ['40 of 40 asked NOT RUN', _line('INCONCLUSIVE')],
        ['ran=0', _line('INCONCLUSIVE')],
    ],
)
def test_an_inconclusive_that_never_started_is_not_a_verdict(tmp_path: Path, log_lines: list[str]) -> None:
    """User ruling 2026-10-04: zero tests run is NO verdict -- refused, and the remedy says wait or re-run."""
    log = _anchor(tmp_path, 'gate.log', *log_lines)
    gap = tmp_path / 'gap.md'
    for decided in (
        admission.admit(
            [('gate', log)], destination=admission.LANE, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV, gap=gap
        ),
        admission.decide_fresh(log, destination=admission.LANE, gap=gap),
    ):
        assert not decided.allowed
        assert 'never started' in decided.message
        assert 're-run' in decided.message
    assert not gap.exists()


def test_an_inconclusive_cut_short_still_admits_a_lane_and_records_the_gap(tmp_path: Path) -> None:
    """The other side: it started, then a wall / node down / truncation cut it -- a lane may carry it."""
    log = _anchor(tmp_path, 'gate.log', '7 of 40 asked NOT RUN', 'ran=33', _line('INCONCLUSIVE'))
    gap = tmp_path / 'gap.md'
    decided = admission.decide_fresh(log, destination=admission.LANE, gap=gap)
    assert decided.allowed
    assert gap.is_file()


@pytest.mark.parametrize(('result', 'allowed'), [('PASS', True), ('FAIL', True), ('INCONCLUSIVE', False)])
def test_an_integration_branch_refuses_an_inconclusive(tmp_path: Path, result: str, *, allowed: bool) -> None:
    """User ruling 2026-10-07: a lane on a slow box may carry INCONCLUSIVE; an integration branch may not."""
    gate = _anchor(tmp_path, 'gate.verdict', _line(result))
    decided = admission.admit(
        [('gate', gate)], destination=admission.INTEGRATION, trunk_tiers=('heavy',), head=HEAD, clean=True, env=ENV
    )
    assert decided.allowed is allowed
    assert admission.decide_fresh(gate, destination=admission.INTEGRATION).allowed is allowed


def _no_merges(monkeypatch) -> None:
    monkeypatch.setattr(admission, '_tree_state', lambda _root: (HEAD, True))
    monkeypatch.setattr(admission, '_current_env', lambda: ENV)
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ())


def test_the_cli_routes_the_declared_integration_branch_to_its_bar(tmp_path: Path, monkeypatch) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('INCONCLUSIVE'))
    _no_merges(monkeypatch)
    declared = '[tool.lab_commons.integrator]\nintegration = "int/x"\n'
    (tmp_path / 'pyproject.toml').write_text(declared, encoding='utf-8')
    base = ['--root', str(tmp_path), '--trunk-ref', 'none', '--anchor', f'gate={gate}']
    assert admission.main([*base, '--remote-ref', 'refs/heads/integrate/main']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/int/x']) == 1


def test_the_cli_refuses_a_push_carrying_an_unnamed_merge_deviation(tmp_path: Path, monkeypatch, capsys) -> None:
    gate = _anchor(tmp_path, 'gate.verdict', _line('PASS'))
    _no_merges(monkeypatch)
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ('m1',))
    seen: list[tuple[str, ...]] = []

    def refused(_root: Path, merges: tuple[str, ...], roots: tuple[str, ...]) -> tuple[str, ...]:
        seen.append(roots)
        return (f'{merges[0]} LOST test_b',)

    monkeypatch.setattr(admission, 'merge_refusals', refused)
    argv = ['--root', str(tmp_path), '--trunk-ref', 'none', '--remote-ref', 'refs/heads/lane/x']
    argv += ['--anchor', f'gate={gate}']
    assert admission.main(argv) == 1
    assert 'm1 LOST test_b' in capsys.readouterr().out
    assert seen == [('tests',)]
    monkeypatch.setattr(admission, 'merge_refusals', lambda *_: ())
    assert admission.main(argv) == 0


def _git(root: Path, *args: str) -> None:
    command = [_GIT, '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t', *args]
    subprocess.run(command, check=True, capture_output=True, timeout=60)


def test_an_unborn_head_publishes_no_merge_and_a_born_one_names_its_merge(tmp_path: Path) -> None:
    """A fresh repo's first push has no HEAD commit to walk: nothing to audit, never a crash.

    The planted control is the same repo once born: one merge reachable from HEAD and no remote ref,
    so an implementation that returned nothing everywhere would red here rather than pass vacuously.
    """
    _git(tmp_path, 'init', '-q', '-b', 'main')
    assert admission.unpublished_merges(tmp_path) == ()
    _git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'c1')
    _git(tmp_path, 'checkout', '-q', '-b', 'side')
    _git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'c2')
    _git(tmp_path, 'checkout', '-q', 'main')
    _git(tmp_path, 'merge', '-q', '--no-ff', '-m', 'm', 'side')
    assert len(admission.unpublished_merges(tmp_path)) == 1
