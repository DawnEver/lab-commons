"""PUSH ADMISSION, read off the verdict LEDGER: lanes need no verdict, integration and trunk need one.

ONE-RUN-AFTER-INTEGRATION (user ruling 2026-10-08): a lane is never judged on its own -- the main
session merges every ready lane into one integration tree and runs the gate ONCE. So a lane push is
admitted with no verdict at all; an integration push cites a PASS or FAIL recorded in the ledger for
HEAD in this env; the trunk cites only a PASS of a trunk tier. Nothing here parses a log: the runner
wrote the ledger after promotion, and the ledger is the one source admission reads.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lab_commons.dev import admission
from lab_commons.dev.verdictledger import Entry, record, run_test_id

_GIT = shutil.which('git') or 'git'
HEAD = 'abc1234' + 'd' * 33
ENV = 'env0001'
TREE = 'sha256:tree'


def _run(result: str, *, tier: str = 'gate', commit: str = HEAD, env: str = ENV) -> Entry:
    return Entry(TREE, env, run_test_id('gate 12-paths'), result, tier=tier, commit=commit, log='gate.log@d')


def _admit(rows: list[Entry], destination: str, *, clean: bool = True, gap: Path | None = None) -> admission.Admission:
    return admission.admit(
        tuple(rows),
        destination=destination,
        trunk_tiers=('heavy',),
        head=HEAD,
        clean=clean,
        env=ENV,
        host='windows',
        gap=gap,
    )


def _part(platform: str, result: str, left: dict[str, list[str]], *, tier: str = 'heavy', env: str = ENV) -> Entry:
    """A platform part's run entry for HEAD -- the shape :mod:`lab_commons.dev.platformparts` records."""
    return Entry(
        f'commit:{HEAD}' if platform == 'linux' else TREE,
        'hpc:ada:linux-x86_64:python-3.13.1' if platform == 'linux' else env,
        run_test_id(f'{tier} {platform}-part'),
        result,
        tier=tier,
        commit=HEAD,
        log='part@d',
        part=platform,
        left=left,
    )


LINUX_LEFT = {'tests/f.py::t': ['windows'], 'tests/j.py::t': ['windows']}


def test_a_linux_and_a_windows_part_compose_into_an_admitted_verdict() -> None:
    rows = [_part('linux', 'PASS', LINUX_LEFT), _part('windows', 'PASS', {})]
    decided = _admit(rows, admission.INTEGRATION)
    assert decided.allowed, decided.message
    assert decided.message.count('[admission] cited:') == 2
    assert _admit(rows, admission.TRUNK).allowed, 'both parts heavy PASS: the trunk counts the composition'


def test_a_missing_part_is_refused_naming_the_part_and_its_command() -> None:
    """PLANTED CONTROL: the linux part alone leaves two windows-only tests unrun."""
    decided = _admit([_part('linux', 'PASS', LINUX_LEFT)], admission.INTEGRATION)
    assert not decided.allowed
    assert 'missing the windows part' in decided.message
    assert '--platform windows' in decided.message


def test_a_fail_part_composes_a_fail_which_integration_admits_with_its_gap(tmp_path: Path) -> None:
    """PLANTED CONTROL: a FAIL in either part is a composed FAIL -- admitted to integration, never to the trunk."""
    failing = Entry(f'commit:{HEAD}', 'hpc:ada:linux-x86_64:python-3.13.1', 'tests/a.py::t', 'FAIL', 'heavy', HEAD, 'x')
    rows = [_part('linux', 'FAIL', LINUX_LEFT), failing, _part('windows', 'PASS', {})]
    gap = tmp_path / 'gap.md'
    decided = _admit(rows, admission.INTEGRATION, gap=gap)
    assert decided.allowed
    assert 'cited FAIL' in decided.message
    assert 'tests/a.py::t' in gap.read_text(encoding='utf-8')
    assert not _admit(rows, admission.TRUNK).allowed


def test_a_test_no_part_ran_and_one_no_platform_can_run_are_refused_by_name() -> None:
    """PLANTED CONTROL: the windows part also left a test, and one test no declared platform can run."""
    left = {**LINUX_LEFT, 'tests/n.py::t': []}
    rows = [_part('linux', 'PASS', left), _part('windows', 'PASS', {'tests/j.py::t': ['windows'], 'tests/n.py::t': []})]
    decided = _admit(rows, admission.INTEGRATION)
    assert not decided.allowed
    assert 'no declared platform can run 1 test(s): tests/n.py::t' in decided.message
    assert 'tests/j.py::t' in decided.message


def test_a_part_for_this_boxs_platform_counts_only_in_this_env() -> None:
    rows = [_part('linux', 'PASS', LINUX_LEFT), _part('windows', 'PASS', {}, env='another-env')]
    assert not _admit(rows, admission.INTEGRATION).allowed


def test_a_gate_tier_composition_is_not_a_trunk_pass() -> None:
    rows = [_part('linux', 'PASS', LINUX_LEFT, tier='gate'), _part('windows', 'PASS', {}, tier='gate')]
    assert _admit(rows, admission.INTEGRATION).allowed
    assert not _admit(rows, admission.TRUNK).allowed


def test_the_parser_reads_the_last_verdict_in_either_grammar() -> None:
    """The stamp grammar's ONE reader stays here: runners still stamp their logs as evidence."""
    line = f'[verdict tree={HEAD} env={ENV} tier=heavy selector=12-paths] PASS -- detail'
    parsed = admission.parse_verdict_line('noise\n' + line)
    assert parsed is not None
    assert (parsed.env, parsed.tier, parsed.result) == (ENV, 'heavy', 'PASS')
    stamped = 'VERDICT result=inconclusive tree=t1 env=e1 log=sha256:00@3 selector=verify'
    flat = admission.parse_verdict_line(line + '\n' + stamped)
    assert flat is not None
    assert (flat.tree, flat.tier, flat.result) == ('t1', None, 'INCONCLUSIVE')
    assert admission.parse_verdict_line('no verdict here') is None


def test_a_lane_push_needs_no_verdict_of_its_own() -> None:
    """THE RULE: a lane is verified on the integrated tree, never separately."""
    decided = _admit([], admission.LANE, clean=False)
    assert decided.allowed
    assert decided.cited is None
    assert 'ONE-RUN-AFTER-INTEGRATION' in decided.message


@pytest.mark.parametrize('result', ['PASS', 'FAIL'])
def test_an_integration_push_cites_a_finished_run_for_head(result: str) -> None:
    decided = _admit([_run(result)], admission.INTEGRATION)
    assert decided.allowed
    assert decided.cited == _run(result)


@pytest.mark.parametrize(
    ('rows', 'clean', 'why'),
    [
        ([], True, 'no recorded verdict'),
        ([_run('PASS', commit='f' * 40)], True, 'no recorded verdict'),
        ([_run('PASS', env='other')], True, 'no recorded verdict'),
        ([_run('PASS', commit='')], True, 'no recorded verdict'),
        ([_run('PASS')], False, 'dirty'),
    ],
)
def test_an_integration_push_refuses_what_is_not_a_verdict_about_head(
    rows: list[Entry], *, clean: bool, why: str
) -> None:
    """Another commit, another env, a verdict about no commit, or a dirty tree: refused, naming the gate."""
    decided = _admit(rows, admission.INTEGRATION, clean=clean)
    assert not decided.allowed
    assert why in decided.message
    assert 'gate' in decided.message


def test_a_per_test_entry_is_not_a_run_verdict() -> None:
    """One test's PASS says nothing about the selection, so it is never cited for a push."""
    single = Entry(TREE, ENV, 'tests/a.py::t', 'PASS', tier='gate', commit=HEAD, log='x')
    assert not _admit([single], admission.INTEGRATION).allowed


def test_the_trunk_admits_only_a_pass_of_its_own_tier() -> None:
    assert _admit([_run('PASS', tier='heavy')], admission.TRUNK).allowed
    assert not _admit([_run('FAIL', tier='heavy')], admission.TRUNK).allowed
    assert not _admit([_run('PASS', tier='gate')], admission.TRUNK).allowed


def test_a_pass_is_preferred_over_a_fail_on_the_same_head() -> None:
    decided = _admit([_run('PASS'), _run('FAIL')], admission.INTEGRATION)
    assert decided.cited is not None
    assert decided.cited.result == 'PASS'


def test_a_cited_fail_writes_the_gap_record_from_the_ledger(tmp_path: Path) -> None:
    """The failing node ids come from the ledger's own per-test rows for the same tree and env."""
    failing = Entry(TREE, ENV, 'tests/a.py::t', 'FAIL', tier='gate', commit=HEAD, log='x')
    gap = tmp_path / 'gaps' / 'gap.md'
    decided = _admit([_run('FAIL'), failing], admission.INTEGRATION, gap=gap)
    assert decided.allowed
    assert 'tests/a.py::t' in gap.read_text(encoding='utf-8')
    assert str(gap) in decided.message
    gap.unlink()
    _admit([_run('PASS')], admission.INTEGRATION, gap=gap)
    assert not gap.exists(), 'a PASS writes no gap'


def _no_merges(monkeypatch) -> None:
    monkeypatch.setattr(admission, '_tree_state', lambda _root: (HEAD, True))
    monkeypatch.setattr(admission, '_current_env', lambda: ENV)
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ())


def test_the_cli_reads_the_ledger_and_routes_each_destination(tmp_path: Path, monkeypatch) -> None:
    _no_merges(monkeypatch)
    ledger = tmp_path / 'ledger.jsonl'
    record(ledger, [_run('FAIL')])
    base = ['--root', str(tmp_path), '--trunk-ref', 'refs/heads/main', '--trunk-tier', 'heavy']
    base += ['--ledger', str(ledger)]
    assert admission.main([*base, '--remote-ref', 'refs/heads/feat/x']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/integrate/main']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/main']) == 1


def test_the_cli_routes_the_declared_integration_branch_to_its_bar(tmp_path: Path, monkeypatch) -> None:
    _no_merges(monkeypatch)
    declared = '[tool.lab_commons.integrator]\nintegration = "int/x"\n'
    (tmp_path / 'pyproject.toml').write_text(declared, encoding='utf-8')
    base = ['--root', str(tmp_path), '--trunk-ref', 'none', '--ledger', str(tmp_path / 'empty.jsonl')]
    assert admission.main([*base, '--remote-ref', 'refs/heads/integrate/main']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/int/x']) == 1


def test_the_cli_refuses_a_push_carrying_an_unnamed_merge_deviation(tmp_path: Path, monkeypatch, capsys) -> None:
    _no_merges(monkeypatch)
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ('m1',))
    seen: list[tuple[str, ...]] = []

    def refused(_root: Path, merges: tuple[str, ...], roots: tuple[str, ...]) -> tuple[str, ...]:
        seen.append(roots)
        return (f'{merges[0]} LOST test_b',)

    monkeypatch.setattr(admission, 'merge_refusals', refused)
    argv = ['--root', str(tmp_path), '--trunk-ref', 'none', '--remote-ref', 'refs/heads/lane/x']
    argv += ['--ledger', str(tmp_path / 'empty.jsonl')]
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
