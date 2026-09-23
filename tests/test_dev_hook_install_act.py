"""``lab_commons.dev.hook_install.install`` -- the ONE install act every family repo's bootstrap calls.

THE GAP THIS CLOSES (user directive 2026-09-23, "guarantee pre-commit hooks by test or mechanism"):
the measurement existed and nothing ever acted on it, so a fresh clone or a fresh worktree carried a
declaration and no hooks until a human read a red and typed the remedy. ``install`` is the act, and
it is shared so no repo restates the stage list -- the defect two Makefiles in this family still had.

WHAT MAKES IT SAFE TO CALL FROM EVERY SEED PATH, each arm below planted in a real repository:

* IDEMPOTENT. A protected checkout runs nothing -- a worktree shares its main checkout's hooks
  directory, so the second caller must learn it has nothing to do rather than rewrite live files.
* FOREIGN IS REFUSED, NEVER OVERWRITTEN. ``pre-commit install`` over a hand-written hook moves it
  aside at best; somebody else's hook is a decision this act may not make for them.
* THE RESULT IS RE-MEASURED. A runner that exits 0 and installs nothing is a pass-shaped no-op, so
  the report returned is the one read AFTER the act, and anything short of protected raises.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab_commons.dev.hook_install import (
    NOTHING_DECLARED,
    PROTECTED,
    HookInstallError,
    generated_hook,
    hook_installation,
    hooks_dir,
    install,
    install_command,
)

_GIT = shutil.which('git') or 'git'

_CONFIG = """\
repos:
  - repo: https://example.invalid/a
    rev: v1
    hooks:
      - id: ruff
      - id: gate
        stages: [pre-push]
"""


def _git(*args: str) -> None:
    subprocess.run([_GIT, *args], check=True, capture_output=True, timeout=60)


def _repo(at: Path, *, config: str | None = _CONFIG) -> Path:
    at.mkdir(parents=True, exist_ok=True)
    _git('init', '-q', str(at))
    if config is not None:
        (at / '.pre-commit-config.yaml').write_text(config, encoding='utf-8')
    return at


class _Recorder:
    """Stands in for ``pre-commit install``: records the argv and writes pre-commit's own shim."""

    def __init__(self, *, writes: bool = True, returncode: int = 0) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.writes = writes
        self.returncode = returncode

    def __call__(self, argv: list[str], cwd: Path) -> int:
        self.calls.append((argv, cwd))
        if self.writes:
            directory = hooks_dir(cwd)
            directory.mkdir(parents=True, exist_ok=True)
            stages = [argv[i + 1] for i, arg in enumerate(argv) if arg == '-t']
            for stage in stages:
                (directory / stage).write_text(
                    generated_hook(config_name='.pre-commit-config.yaml', hook_type=stage), encoding='utf-8'
                )
        return self.returncode


def test_an_unprotected_checkout_is_installed_with_the_derived_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path / 'r')
    expected = [sys.executable, *install_command(repo)]
    runner = _Recorder()
    report = install(repo, run=runner)
    assert report.verdict == PROTECTED
    assert [argv for argv, _ in runner.calls] == [expected], 'the argv is DERIVED, never restated'


def test_a_protected_checkout_runs_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path / 'r')
    install(repo, run=_Recorder())
    again = _Recorder()
    assert install(repo, run=again).verdict == PROTECTED
    assert again.calls == [], 'a second install over live hooks is a mutation with nothing to gain'


def test_a_worktree_installs_into_the_shared_directory_once(tmp_path: Path) -> None:
    """A worktree has no hooks directory of its own; the main checkout must then read protected."""
    main = _repo(tmp_path / 'main')
    _git('-C', str(main), '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.pre-commit-config.yaml')
    _git('-C', str(main), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'init')
    tree = tmp_path / 'tree'
    _git('-C', str(main), 'worktree', 'add', '-q', str(tree))
    install(tree, run=_Recorder())
    assert hooks_dir(tree).resolve() == hooks_dir(main).resolve()
    after = _Recorder()
    assert install(main, run=after).verdict == PROTECTED
    assert after.calls == []


def test_somebody_elses_hook_is_refused_and_left_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path / 'r')
    directory = hooks_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    mine = directory / 'pre-commit'
    mine.write_text('#!/bin/sh\necho mine\n', encoding='utf-8')
    runner = _Recorder()
    with pytest.raises(HookInstallError, match='pre-commit'):
        install(repo, run=runner)
    assert runner.calls == []
    assert mine.read_text(encoding='utf-8') == '#!/bin/sh\necho mine\n'


def test_a_runner_that_installs_nothing_is_not_a_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path / 'r')
    with pytest.raises(HookInstallError, match='still'):
        install(repo, run=_Recorder(writes=False))
    with pytest.raises(HookInstallError, match='exit'):
        install(repo, run=_Recorder(returncode=3))


def test_nothing_declared_is_reported_and_nothing_runs(tmp_path: Path) -> None:
    repo = _repo(tmp_path / 'r', config=None)
    runner = _Recorder()
    assert install(repo, run=runner).verdict == NOTHING_DECLARED
    assert runner.calls == []
    assert hook_installation(repo).verdict == NOTHING_DECLARED
