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
    monkeypatch.setattr(collect._door, 'run', _fake_run('', returncode))
    out = tmp_path / 'out.txt'
    collect.main(['--cache', str(tmp_path / 'c'), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert not list((tmp_path / 'c').rglob('*.json')), 'a failed run poisoned the cache with empty id lists'
    assert out.read_text(encoding='utf-8').startswith('ERROR '), 'the failure must reach the reader'


def test_a_clean_run_still_caches_its_ids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collect._door, 'run', _fake_run('tests/test_a.py::test_x\n', 0))
    out = tmp_path / 'out.txt'
    collect.main(['--cache', str(tmp_path / 'c'), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert len(list((tmp_path / 'c').rglob('*.json'))) == 1
    assert out.read_text(encoding='utf-8') == 'tests/test_a.py::test_x\n'


def test_a_key_is_one_file_however_many_test_files_it_caches(tmp_path: Path, monkeypatch) -> None:
    """FILES ARE A RESOURCE: one cache file per global key, never one per test file (1 M files measured)."""
    listing = b''.join(f'100644 {i:040x} 0\ttests/test_{i}.py\0'.encode() for i in range(50))
    ids = ''.join(f'tests/test_{i}.py::t\n' for i in range(50))
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[Any]:
        calls.append(argv)
        if argv[1:3] == ['ls-files', '-s']:
            return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr=b'')
        return subprocess.CompletedProcess(argv, 0, stdout=ids, stderr='')

    monkeypatch.setattr(collect._door, 'run', run)
    cache, out = tmp_path / 'c', tmp_path / 'out.txt'
    collect.main(['--cache', str(cache), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert [p.suffix for p in cache.rglob('*')] == ['.json'], 'one file, directly under the cache'
    calls.clear()
    collect.main(['--cache', str(cache), '--key', 'k', '--out', str(out), '--', 'tests'])
    assert len(calls) == 1, 'a warm key runs only ls-files'
    assert out.read_text(encoding='utf-8') == ids
