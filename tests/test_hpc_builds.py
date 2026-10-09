"""Cluster build reuse keyed by declared inputs, and the per-file collection cache.

The collector is exercised by REALLY running it (git + pytest in a temporary repository), because what it
saves is a pytest call: a fake could not show which files it collected again.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc.builds import builds_at, read_builds

_GIT: Final = shutil.which('git') or 'git'
COLLECTOR: Final = Path(__file__).parents[1] / 'src' / 'lab_commons' / 'hpc' / 'collect.py'

PYPROJECT: Final = """[project]
name = "x"
[tool.lab_commons.hpc]
dependency_inputs = ["pyproject.toml", "uv.lock"]
source_roots = ["src"]
native = { inputs = ["rust"], build = "make" }
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([_GIT, '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding='utf-8')
    _git(repo, 'add', '-A', '.')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'c')
    return _git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / 'repo'
    path.mkdir()
    _git(path, 'init', '-q')
    return path


def test_the_declaration_is_read_once_and_refuses_a_shared_venv_without_source_roots() -> None:
    builds = read_builds(PYPROJECT)
    assert (builds.dependency_inputs, builds.source_roots) == (('pyproject.toml', 'uv.lock'), ('src',))
    assert (builds.native_inputs, builds.native_build) == (('rust',), 'make')
    assert read_builds('[project]\nname = "x"\n').dependency_inputs == ()
    with pytest.raises(ValueError, match='needs source_roots'):
        read_builds('[tool.lab_commons.hpc]\ndependency_inputs = ["uv.lock"]\n')


def test_a_new_sha_with_unchanged_inputs_keeps_both_keys_and_a_changed_input_moves_only_its_own(repo: Path) -> None:
    first = _commit(repo, {'pyproject.toml': PYPROJECT, 'uv.lock': 'a', 'src/m.py': '1', 'rust/lib.rs': 'r'})
    source_only = _commit(repo, {'src/m.py': '2'})
    native_moved = _commit(repo, {'rust/lib.rs': 's'})
    lock_moved = _commit(repo, {'uv.lock': 'b'})
    keys = [builds_at(repo, sha, install='uv sync', python='3.13') for sha in (first, source_only, native_moved)]
    assert keys[0].env_key == keys[1].env_key == keys[2].env_key, 'a source edit or a rust edit keeps the venv'
    assert keys[0].native_key == keys[1].native_key != keys[2].native_key
    moved = builds_at(repo, lock_moved, install='uv sync', python='3.13')
    assert moved.env_key != keys[0].env_key
    assert builds_at(repo, first, install='uv sync --extra all', python='3.13').env_key != keys[0].env_key


def _collect(repo: Path, cache: Path) -> list[str]:
    argv = [sys.executable, str(COLLECTOR), '--cache', str(cache), '--key', 'k', '--', 'tests']
    return subprocess.run(argv, cwd=repo, capture_output=True, text=True, check=True).stdout.splitlines()


def test_only_files_whose_content_changed_are_collected_again(repo: Path, tmp_path: Path) -> None:
    _commit(
        repo,
        {
            'tests/test_a.py': 'def test_one():\n    pass\n',
            'tests/test_b.py': 'def test_two():\n    pass\n',
            'tests/test_broken.py': 'import nowhere_at_all\n',
            'pyproject.toml': '[project]\nname = "x"\n',
        },
    )
    cache = tmp_path / 'cache'
    first = _collect(repo, cache)
    assert 'tests/test_a.py::test_one' in first
    assert 'tests/test_b.py::test_two' in first
    assert any(line.startswith('ERROR tests/test_broken.py') for line in first)
    [store] = cache.iterdir()
    assert len(list(store.iterdir())) == 2, 'a file that failed to collect is never cached'
    (store / f'{_git(repo, "rev-parse", "HEAD:tests/test_a.py")}.json').write_text(
        json.dumps(['tests/test_a.py::from_the_cache']), encoding='utf-8'
    )
    _commit(repo, {'tests/test_b.py': 'def test_three():\n    pass\n'})
    second = _collect(repo, cache)
    assert 'tests/test_a.py::from_the_cache' in second, 'an unchanged file is answered from the cache'
    assert 'tests/test_b.py::test_three' in second, 'a changed file is collected again'
    _commit(repo, {'tests/conftest.py': ''})
    assert 'tests/test_a.py::test_one' in _collect(repo, cache), 'a conftest change re-keys every file'
