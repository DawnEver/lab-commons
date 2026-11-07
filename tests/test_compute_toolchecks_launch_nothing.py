"""A CHECK LAUNCHES NOTHING: every :data:`CHECKS` row reads the install, never starts the tool.

On Windows a vendor executable handed a flag may open a window instead of answering (Electronics Desktop
Student's ``ansysedtsv.exe -help`` showed a modal help dialog, 2026-11-07; Fluent and others pop up too), and
an unattended probe then blocks until its timeout. One rule, one test, no per-tool case.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from lab_commons.compute.toolchecks import CHECKS, LSDYNA_SOLVERS

#: Every way Python starts another program; each is replaced by the recorder.
_STARTERS = ('Popen', 'startfile', 'system', 'spawnv', 'spawnve', 'spawnl', 'execv', 'execve', 'posix_spawn')


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every program start in this process is recorded and refused -- the fake executables' launch log."""
    log: list[object] = []

    def record(*args: object, **_kwargs: object) -> None:
        log.append(args[0] if args else None)
        msg = f'a check launched {args[0] if args else "a program"}'
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, 'Popen', record)
    for name in _STARTERS[1:]:
        if hasattr(os, name):
            monkeypatch.setattr(os, name, record)
    return log


def _fake_install(tmp_path: Path, tool: str) -> Path:
    """A fake executable for *tool* under a release-named directory, with the LS-DYNA solver beside it."""
    root = tmp_path / tool / 'v252'
    root.mkdir(parents=True)
    exe = root / f'{tool}.exe'
    exe.write_bytes(b'MZ fake')
    exe.chmod(0o755)
    for solver in LSDYNA_SOLVERS:
        (root / solver).write_bytes(b'MZ fake')
        (root / solver).chmod(0o755)
    return exe


def test_the_recorder_catches_a_check_that_launches(launched: list[object], tmp_path: Path) -> None:
    """PLANTED CONTROL: a check whose smoke starts its executable is caught, so an empty log below means something."""
    exe = _fake_install(tmp_path, 'aedt')

    def help_flag(path: Path) -> tuple[bool, str]:
        done = subprocess.run([str(path), '-help'], check=False)
        return done.returncode == 0, ''

    launching = replace(CHECKS['aedt'], smoke=help_flag)
    with pytest.raises(AssertionError, match='launched'):
        launching.smoke(exe)
    assert launched == [[str(exe), '-help']]


@pytest.mark.parametrize('tool', sorted(CHECKS))
def test_a_check_launches_nothing(launched: list[object], tmp_path: Path, tool: str) -> None:
    check = CHECKS[tool]
    exe = _fake_install(tmp_path, tool)
    check.find()
    check.version(exe)
    passed, detail = check.smoke(exe)
    assert launched == []
    assert passed, f'{tool}: an installed executable passes its smoke: {detail}'


@pytest.mark.parametrize('tool', sorted(CHECKS))
def test_a_check_fails_where_nothing_is_installed(launched: list[object], tmp_path: Path, tool: str) -> None:
    passed, _detail = CHECKS[tool].smoke(tmp_path / 'v252' / f'{tool}.exe')
    assert not passed
    assert launched == []


def test_an_ansys_release_is_read_off_its_path(tmp_path: Path) -> None:
    assert CHECKS['aedt'].version(_fake_install(tmp_path, 'aedt')) == ('install path', '2025 R2')
