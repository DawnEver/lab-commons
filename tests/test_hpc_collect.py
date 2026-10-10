"""The cluster collector never caches the result of a pytest run that failed as a whole."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lab_commons.hpc import collect


def _fake_run(stdout: str, returncode: int) -> Callable[..., subprocess.CompletedProcess[Any]]:
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[Any]:
        if argv[1:3] == ['ls-files', '-s']:
            return subprocess.CompletedProcess(argv, 0, stdout=b'100644 aaaa 0\ttests/test_a.py\0', stderr=b'')
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr='ERROR: bad -m expression\n')

    return run


@pytest.mark.parametrize('returncode', [2, 3, 4])
def test_a_failed_collection_run_is_reported_and_never_cached(tmp_path: Path, monkeypatch, returncode: int) -> None:
    monkeypatch.setattr(collect.subprocess, 'run', _fake_run('', returncode))
    out = tmp_path / 'out.txt'
    collect.main(['--cache', str(tmp_path / 'c'), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert not list((tmp_path / 'c').rglob('*.json')), 'a failed run poisoned the cache with empty id lists'
    assert out.read_text(encoding='utf-8').startswith('ERROR '), 'the failure must reach the reader'


def test_a_clean_run_still_caches_its_ids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collect.subprocess, 'run', _fake_run('tests/test_a.py::test_x\n', 0))
    out = tmp_path / 'out.txt'
    collect.main(['--cache', str(tmp_path / 'c'), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert len(list((tmp_path / 'c').rglob('*.json'))) == 1
    assert out.read_text(encoding='utf-8') == 'tests/test_a.py::test_x\n'
