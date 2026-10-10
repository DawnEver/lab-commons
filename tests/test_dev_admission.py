"""PUSH ADMISSION, read off the FORGE: lanes and integration need no part, the trunk needs every part green.

Forge plan section 6 (user rulings 2026-10-09): parts are produced FROM pushed shas, so only the trunk
is judged, on HEAD's ``tests/<platform>`` statuses, from any workstation -- and when the forge cannot be
read the trunk WAITS; the local ledger is never consulted.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lab_commons.dev import admission
from lab_commons.dev.forge import NoCredential
from lab_commons.dev.forgestatus import Status
from lab_commons.dev.platformparts import Part, part_context

_GIT = shutil.which('git') or 'git'
HEAD = 'abc1234' + 'd' * 33


def _status(platform: str, result: str = 'PASS', *, scope: str = 'full', hands: dict[str, int] | None = None) -> Status:
    part = Part(platform, scope, result, ran=10, hands=hands or {})
    return Status(part_context(platform), part.state, part.description(), 'https://forge/attachments/1')


def _trunk(statuses: list[Status]) -> admission.Admission:
    return admission.admit(admission.TRUNK, head=HEAD, statuses=lambda: statuses)


@pytest.mark.parametrize('destination', [admission.LANE, admission.INTEGRATION])
def test_a_lane_and_an_integration_push_need_no_part(destination: str) -> None:
    """Parts are produced from pushed shas; asking for one before the push would deadlock."""

    def never() -> list[Status]:
        pytest.fail('a lane or integration push read the forge')

    decided = admission.admit(destination, head=HEAD, statuses=never)
    assert decided.allowed
    assert 'no part' in decided.message


@pytest.mark.parametrize('order', ['linux first', 'windows first'])
def test_a_linux_and_a_windows_part_compose_in_either_order(order: str) -> None:
    statuses = [_status('linux', hands={'windows': 2}), _status('windows')]
    decided = _trunk(statuses if order == 'linux first' else statuses[::-1])
    assert decided.allowed, decided.message
    assert 'linux=PASS/full' in decided.message
    assert 'windows=PASS/full' in decided.message


def test_a_missing_part_is_refused_naming_the_part_and_its_command() -> None:
    """PLANTED CONTROL: linux hands two ids to windows, and no windows part was posted."""
    decided = _trunk([_status('linux', hands={'windows': 2})])
    assert not decided.allowed
    assert 'missing the windows part (linux hands 2)' in decided.message
    assert '--platform windows' in decided.message


@pytest.mark.parametrize(
    ('statuses', 'why'),
    [
        ([], 'no part is posted'),
        ([_status('linux', 'FAIL')], 'linux is FAIL of scope full'),
        ([_status('linux', scope='incremental')], 'linux is PASS of scope incremental'),
        ([Status('tests/linux', 'pending', 'claim machine=G login=a until=x', '')], 'not a settled part'),
        ([Status('tests/linux', 'success', 'free text', '')], 'not a settled part'),
        ([Status('tests/linux', 'success', 'scope=full ran=3 hands=- nowhere=2', '')], 'no declared platform'),
    ],
)
def test_the_trunk_refuses_anything_but_every_part_a_full_pass(statuses: list[Status], why: str) -> None:
    """PLANTED CONTROLS: nothing, a FAIL, an incremental part, a claim, a foreign description, unrunnable ids."""
    decided = _trunk(statuses)
    assert not decided.allowed
    assert why in decided.message


def test_other_contexts_are_not_parts() -> None:
    assert _trunk([_status('linux'), Status('lab/gate', 'failure', 'VERDICT result=fail', '')]).allowed


@pytest.mark.parametrize('error', [NoCredential('no stored credential'), OSError('connection refused')])
def test_the_trunk_waits_when_the_forge_cannot_be_read(error: Exception) -> None:
    """Gitea down => main waits: no local fallback stands in for the forge."""

    def down() -> list[Status]:
        raise error

    decided = admission.admit(admission.TRUNK, head=HEAD, statuses=down)
    assert not decided.allowed
    assert 'WAITS' in decided.message
    assert 'no local fallback' in decided.message


def test_the_parser_reads_the_last_verdict_in_either_grammar() -> None:
    """The stamp grammar's ONE reader stays here: runners still stamp their logs as evidence."""
    line = f'[verdict tree={HEAD} env=e0 tier=heavy selector=12-paths] PASS -- detail'
    parsed = admission.parse_verdict_line('noise\n' + line)
    assert parsed is not None
    assert (parsed.env, parsed.tier, parsed.result) == ('e0', 'heavy', 'PASS')
    stamped = 'VERDICT result=inconclusive tree=t1 env=e1 log=sha256:00@3 selector=verify'
    flat = admission.parse_verdict_line(line + '\n' + stamped)
    assert flat is not None
    assert (flat.tree, flat.tier, flat.result) == ('t1', None, 'INCONCLUSIVE')
    assert admission.parse_verdict_line('no verdict here') is None


def _no_merges(monkeypatch: pytest.MonkeyPatch, statuses: list[Status]) -> None:
    monkeypatch.setattr(admission, '_head', lambda _root: HEAD)
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ())
    monkeypatch.setattr(admission, 'unprompted_client', lambda _root: None)
    monkeypatch.setattr(admission, 'status_list', lambda _client, _sha: statuses)


def test_the_cli_reads_the_forge_and_routes_each_destination(tmp_path: Path, monkeypatch) -> None:
    _no_merges(monkeypatch, [_status('linux', 'FAIL')])
    base = ['--root', str(tmp_path), '--trunk-ref', 'refs/heads/main']
    assert admission.main([*base, '--remote-ref', 'refs/heads/feat/x']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/integrate/main']) == 0
    assert admission.main([*base, '--remote-ref', 'refs/heads/main']) == 1
    _no_merges(monkeypatch, [_status('linux')])
    assert admission.main([*base, '--remote-ref', 'refs/heads/main']) == 0


def test_the_cli_refuses_a_push_carrying_an_unnamed_merge_deviation(tmp_path: Path, monkeypatch, capsys) -> None:
    _no_merges(monkeypatch, [])
    monkeypatch.setattr(admission, 'unpublished_merges', lambda _root: ('m1',))
    seen: list[tuple[str, ...]] = []

    def refused(_root: Path, merges: tuple[str, ...], roots: tuple[str, ...]) -> tuple[str, ...]:
        seen.append(roots)
        return (f'{merges[0]} LOST test_b',)

    monkeypatch.setattr(admission, 'merge_refusals', refused)
    argv = ['--root', str(tmp_path), '--trunk-ref', 'none', '--remote-ref', 'refs/heads/lane/x']
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
