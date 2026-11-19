"""Cross-session state of one repo: ``<machine home>/state/<repo>/<kind>/``, one key per repo across worktrees."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lab_commons import home, repostate

_GIT = shutil.which('git') or 'git'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([_GIT, *args], cwd=cwd, check=True, capture_output=True)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, 'init', '-q')
    _git(path, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'root')
    return path


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / 'motronics-home'
    monkeypatch.setenv(home.HOME_ENV, str(root))
    return root


def test_every_worktree_of_one_repo_shares_one_state_dir(machine: Path, tmp_path: Path) -> None:
    main = _repo(tmp_path / 'clone' / 'studio')
    tree = main / '.claude' / 'worktrees' / 'lane'
    _git(main, 'worktree', 'add', '-q', '-b', 'lane', str(tree))
    nested = tree / 'src'
    nested.mkdir()
    want = repostate.state_dir(main, 'bindings')
    assert want.parent.parent == machine / 'state'
    assert want.name == 'bindings'
    assert want.parent.name.startswith('studio-')
    assert repostate.state_dir(tree, 'bindings') == want
    assert repostate.state_dir(nested, 'bindings') == want


@pytest.mark.usefixtures('machine')
def test_two_clones_of_one_name_get_two_state_dirs(tmp_path: Path) -> None:
    """The planted control: same basename, different checkouts -- a name-only key would say yes here."""
    first = _repo(tmp_path / 'a' / 'studio')
    second = _repo(tmp_path / 'b' / 'studio')
    assert repostate.repo_key(first) != repostate.repo_key(second)
    assert repostate.state_dir(first, 'consent') != repostate.state_dir(second, 'consent')


@pytest.mark.usefixtures('machine')
def test_two_kinds_of_one_repo_are_siblings(tmp_path: Path) -> None:
    main = _repo(tmp_path / 'studio')
    assert repostate.state_dir(main, 'a').parent == repostate.state_dir(main, 'b').parent


@pytest.mark.usefixtures('machine')
@pytest.mark.parametrize('kind', ['', '.', '..', 'a/b', 'a\\b'])
def test_a_kind_that_is_not_one_name_is_refused(tmp_path: Path, kind: str) -> None:
    main = _repo(tmp_path / 'studio')
    with pytest.raises(ValueError, match='one directory name'):
        repostate.state_dir(main, kind)


@pytest.mark.usefixtures('machine')
def test_a_path_outside_every_repo_is_refused_by_name(tmp_path: Path) -> None:
    loose = tmp_path / 'loose'
    loose.mkdir()
    with pytest.raises(repostate.NotARepo, match='not inside a git repository'):
        repostate.repo_key(loose)


def test_nothing_is_created(machine: Path, tmp_path: Path) -> None:
    repostate.state_dir(_repo(tmp_path / 'studio'), 'bindings')
    assert not machine.exists()
