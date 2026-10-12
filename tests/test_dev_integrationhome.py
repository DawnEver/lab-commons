"""INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT, over a REAL repository with a REAL linked worktree.

The branch names are DECLARED non-defaults (``int/line``, ``stable``) so a reader that guessed
``integrate/main``/``main`` instead of reading ``[tool.lab_commons.integrator]`` reads green on nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab_commons.dev import integrationhome
from lab_commons.dev.integrationhome import refusal, staleness

_GIT = shutil.which('git') or 'git'
_DECL = "[tool.lab_commons.integrator]\nintegration = 'int/line'\ntrunk = 'stable'\n"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [_GIT, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An origin, a main checkout on ``stable`` with ``int/line`` and ``lane/x`` beside it."""
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '--bare', '-b', 'stable', str(origin))
    main = tmp_path / 'main'
    _git(tmp_path, 'clone', str(origin), str(main))
    (main / 'pyproject.toml').write_text(_DECL, encoding='utf-8')
    _git(main, 'checkout', '-b', 'stable')
    _git(main, 'add', 'pyproject.toml')
    _git(main, 'commit', '-m', 'chore: seed')
    _git(main, 'branch', 'int/line')
    _git(main, 'branch', 'lane/x')
    _git(main, 'push', 'origin', 'stable', 'int/line')
    return main


def _tree(main: Path, name: str, branch: str) -> Path:
    path = main / '.claude' / 'worktrees' / name
    _git(main, 'worktree', 'add', str(path), branch)
    return path


def test_a_linked_worktree_on_the_integration_branch_is_refused_and_names_the_remedy(repo: Path) -> None:
    reason = refusal(_tree(repo, 'int', 'int/line'))
    assert reason is not None
    assert 'int/line' in reason
    assert 'main checkout' in reason
    assert 'lane branch' in reason


def test_a_linked_worktree_on_the_trunk_is_refused(repo: Path) -> None:
    _git(repo, 'checkout', 'lane/x')
    assert refusal(_tree(repo, 'trunk', 'stable')) is not None


def test_a_lane_in_a_linked_worktree_and_the_main_checkout_itself_are_permitted(repo: Path) -> None:
    assert refusal(_tree(repo, 'lane', 'lane/x')) is None
    _git(repo, 'checkout', 'int/line')
    assert refusal(repo) is None


def _install_commit_msg(main: Path) -> None:
    hooks = main / 'hooks'
    hooks.mkdir()
    python = Path(sys.executable).as_posix()
    (hooks / 'commit-msg').write_text(
        f'#!/bin/sh\nexec "{python}" -m lab_commons.dev.integrationhome commit-msg "$@"\n',
        encoding='utf-8',
        newline='\n',
    )
    (hooks / 'commit-msg').chmod(0o755)
    subprocess.run([_GIT, 'config', 'core.hooksPath', hooks.as_posix()], cwd=main, check=True)


def _commit(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_GIT, '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        timeout=120,
    )


def test_the_commit_time_hook_refuses_a_commit_and_a_merge_commit_from_a_linked_worktree(repo: Path) -> None:
    """End to end through git: the merge commit is the measured incident (2026-10-12)."""
    lane = _tree(repo, 'lane', 'lane/x')
    (lane / 'a.txt').write_text('a\n', encoding='utf-8')
    _git(lane, 'add', 'a.txt')
    _git(lane, 'commit', '-m', 'feat: a')
    _install_commit_msg(repo)
    home = _tree(repo, 'int', 'int/line')
    (home / 'b.txt').write_text('b\n', encoding='utf-8')
    _git(home, 'add', 'b.txt')
    plain = _commit(home, 'commit', '-m', 'feat: b')
    assert plain.returncode != 0, plain.stdout
    assert 'INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT' in plain.stderr
    _git(home, 'reset', '-q', 'HEAD', '--', 'b.txt')
    merge = _commit(home, 'merge', '--no-ff', '-m', 'chore(merge): lane/x', 'lane/x')
    assert merge.returncode != 0, merge.stdout
    assert 'INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT' in merge.stderr
    assert _commit(lane, 'commit', '--allow-empty', '-m', 'feat: lane').returncode == 0


def test_staleness_names_the_main_checkouts_lag_and_the_fast_forward(repo: Path, tmp_path: Path) -> None:
    _git(repo, 'checkout', 'int/line')
    assert staleness(repo) is None
    other = tmp_path / 'other'
    _git(tmp_path, 'clone', '-b', 'int/line', str(tmp_path / 'origin.git'), str(other))
    for n in range(2):
        _git(other, 'commit', '--allow-empty', '-m', f'feat: {n}')
    _git(other, 'push', 'origin', 'int/line')
    _git(repo, 'fetch', 'origin')
    lane = _tree(repo, 'lane', 'lane/x')
    for where in (repo, lane):
        note = staleness(where)
        assert note is not None
        assert 'int/line' in note
        assert 'behind origin/int/line by 2' in note
        assert 'with-retry.sh pull --ff-only' in note


def test_the_report_door_exits_zero_and_prints_the_lag(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert integrationhome.main(['report', '--root', str(repo)]) == 0
    assert 'integration-home' in capsys.readouterr().out
