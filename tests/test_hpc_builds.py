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

from lab_commons.hpc.builds import ItemWall, builds_at, read_builds

_GIT: Final = shutil.which('git') or 'git'
COLLECTOR: Final = Path(__file__).parents[1] / 'src' / 'lab_commons' / 'hpc' / 'collect.py'

PYPROJECT: Final = """[project]
name = "x"
[tool.lab_commons.hpc]
install = "uv sync"
dependency_inputs = ["pyproject.toml", "uv.lock"]
source_roots = ["src"]
native = { inputs = ["rust"], build = "make" }
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True
    ).stdout.strip()


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


def _shared(install: str) -> str:
    text = f'[tool.lab_commons.hpc]\ninstall = "{install}"\ndependency_inputs = ["uv.lock"]\nsource_roots = ["src"]\n'
    return read_builds(text).install


def test_a_shared_venv_holds_dependencies_only_so_each_tree_imports_its_own_project() -> None:
    """A shared venv's install never carries the project, which would point into one tree.

    Measured 2026-10-25 (numerics-lab #33): an editable project install in a SHARED venv points at the tree that
    built it, so every later sha reusing the venv imported -- or failed to import -- the FIRST tree's project.
    """
    assert _shared('uv sync --frozen --extra dev') == 'uv sync --frozen --extra dev --no-install-project'
    declared = 'uv sync --frozen --no-install-project --extra dev'
    assert _shared(declared) == declared
    # planted control: a venv per commit belongs to its one tree, so its project install stays as declared
    assert read_builds('[tool.lab_commons.hpc]\ninstall = "uv sync"\n').install == 'uv sync'
    with pytest.raises(ValueError, match='dependencies only'):
        _shared('pip install -e .')


def test_a_new_sha_with_unchanged_inputs_keeps_both_keys_and_a_changed_input_moves_only_its_own(repo: Path) -> None:
    first = _commit(repo, {'pyproject.toml': PYPROJECT, 'uv.lock': 'a', 'src/m.py': '1', 'rust/lib.rs': 'r'})
    source_only = _commit(repo, {'src/m.py': '2'})
    native_moved = _commit(repo, {'rust/lib.rs': 's'})
    lock_moved = _commit(repo, {'uv.lock': 'b'})
    keys = [builds_at(repo, sha, python='3.13') for sha in (first, source_only, native_moved)]
    assert keys[0].env_key == keys[1].env_key == keys[2].env_key, 'a source edit or a rust edit keeps the venv'
    assert keys[0].native_key == keys[1].native_key != keys[2].native_key
    moved = builds_at(repo, lock_moved, python='3.13')
    assert moved.env_key != keys[0].env_key
    reinstalled = _commit(repo, {'pyproject.toml': PYPROJECT.replace('"uv sync"', '"uv sync --extra all"')})
    assert builds_at(repo, reinstalled, python='3.13').env_key not in {k.env_key for k in keys}


UNDECLARED: Final = '[project]\nname = "x"\n[tool.lab_commons.hpc]\ninstall = "uv sync --no-install-project"\n'


def test_every_env_is_keyed_by_every_input_that_builds_it_even_with_no_declared_inputs(repo: Path) -> None:
    """PLANTED, the 2026-10-10 resubmit: a corrected install MUST move the key, or the old env is reused."""
    first = _commit(repo, {'pyproject.toml': UNDECLARED})
    wrong = builds_at(repo, first, python='3.13')
    assert wrong.env_key, 'no env goes unkeyed'
    fixed = _commit(repo, {'pyproject.toml': UNDECLARED.replace(' --no-install-project', '')})
    assert builds_at(repo, fixed, python='3.13').env_key != wrong.env_key
    assert builds_at(repo, first, python='3.12').env_key != wrong.env_key, 'the interpreter request is an input'
    needing = _commit(repo, {'pyproject.toml': UNDECLARED + 'needs = ["cargo"]\n'})
    assert builds_at(repo, needing, python='3.13').needs == ('cargo',)
    assert builds_at(repo, needing, python='3.13').env_key != wrong.env_key, 'the needs are an input'
    assert builds_at(repo, first, python='3.13') == wrong, 'and an unchanged recipe reuses'


def test_a_commit_declaring_no_install_is_refused_naming_the_key(repo: Path) -> None:
    sha = _commit(repo, {'pyproject.toml': '[project]\nname = "x"\n'})
    with pytest.raises(ValueError, match=r'\[tool\.lab_commons\.hpc\] install'):
        builds_at(repo, sha, python='3.13')


def _collect(repo: Path, cache: Path) -> list[str]:
    out = cache.parent / 'collected.txt'
    argv = [sys.executable, str(COLLECTOR), '--cache', str(cache), '--key', 'k', '--out', str(out), '--', 'tests']
    subprocess.run(argv, cwd=repo, check=True)
    return out.read_text(encoding='utf-8').splitlines()


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
    [store] = cache.iterdir()  # ONE file per key: {blob id: ids}
    entries = json.loads(store.read_text(encoding='utf-8'))
    assert len(entries) == 2, 'a file that failed to collect is never cached'
    entries[_git(repo, 'rev-parse', 'HEAD:tests/test_a.py')] = ['tests/test_a.py::from_the_cache']
    store.write_text(json.dumps(entries), encoding='utf-8')
    _commit(repo, {'tests/test_b.py': 'def test_three():\n    pass\n'})
    second = _collect(repo, cache)
    assert 'tests/test_a.py::from_the_cache' in second, 'an unchanged file is answered from the cache'
    assert 'tests/test_b.py::test_three' in second, 'a changed file is collected again'
    _commit(repo, {'tests/conftest.py': ''})
    assert 'tests/test_a.py::test_one' in _collect(repo, cache), 'a conftest change re-keys every file'


GIT_DEPENDENT: Final = PYPROJECT.replace(
    '[tool.lab_commons.hpc]',
    'dependencies = ["dep-a @ git+https://forge.invalid/dep-a"]\n[tool.lab_commons.hpc]',
)


def test_the_lock_keys_the_venv_and_no_remote_is_asked(repo: Path) -> None:
    """PIN + FRESHNESS (2026-10-17): a git dependency's revision is the lock's, so the lock's hash is the key.

    The remote is unresolvable (``.invalid``): a key that asked it would raise. PLANTED CONTROL: a new lock rebuilds.
    """
    before = builds_at(repo, _commit(repo, {'pyproject.toml': GIT_DEPENDENT, 'uv.lock': 'rev = "1"'}), python='3.13')
    assert builds_at(repo, _commit(repo, {'README': 'x'}), python='3.13') == before, 'an unchanged lock reuses'
    after = builds_at(repo, _commit(repo, {'uv.lock': 'rev = "2"'}), python='3.13')
    assert after.env_key != before.env_key, 'a new lock rebuilds the venv'
    assert after.native_key != before.native_key, 'the native build links against the venv it was built in'


def test_the_per_scope_timeout_is_declared_data_and_an_unknown_scope_is_refused() -> None:
    text = '[tool.lab_commons.hpc]\ninstall = "x"\ntimeout_s = {incremental = 300, full = 3000}\n'
    assert read_builds(text).timeout_s == {'incremental': 300, 'full': 3000}
    assert read_builds('[tool.lab_commons.hpc]\ninstall = "x"\n').timeout_s == {}
    with pytest.raises(ValueError, match='timeout_s'):
        read_builds('[tool.lab_commons.hpc]\ninstall = "x"\ntimeout_s = {heavy = 3000}\n')
    with pytest.raises(ValueError, match='timeout_s'):
        read_builds('[tool.lab_commons.hpc]\ninstall = "x"\ntimeout_s = {full = 0}\n')


def test_no_test_wall_passes_the_declared_ceiling_however_inflated_its_measurement() -> None:
    """Ada 2026-10-24..26: eight forge jobs held a core 34-41 h each on ONE test whose wall read 776553..1051465 s.

    The wall was ``multiple`` times a learned kill floor that every wall kill had doubled; nothing capped it, so
    the item runner's kill could never fire before Slurm's. The ceiling holds every wall, measured or not.
    """
    wall = ItemWall(multiple=10, floor_s=300, unmeasured_s=3600, ceiling_s=7200)
    assert wall.bound(104799.0) == 7200, 'the measured floor of the incident, held to the ceiling'
    assert wall.bound(60.0) == 600, 'a sane measurement is untouched'
    assert ItemWall().bound(1e9) == ItemWall().ceiling_s
    text = '[tool.lab_commons.hpc]\ninstall = "x"\nitem_wall = {ceiling_s = 1800}\n'
    assert read_builds(text).item_wall.ceiling_s == 1800
    assert read_builds(text).item_wall.bound(None) == 1800, 'the unmeasured default is held too'
    with pytest.raises(ValueError, match='ceiling_s'):
        read_builds('[tool.lab_commons.hpc]\ninstall = "x"\nitem_wall = {ceiling_s = 200}\n')


def test_the_per_test_wall_is_declared_data_with_one_set_of_defaults() -> None:
    text = '[tool.lab_commons.hpc]\ninstall = "x"\nitem_wall = {multiple = 5, floor_s = 120}\n'
    assert read_builds(text).item_wall == ItemWall(multiple=5, floor_s=120)
    assert read_builds('[tool.lab_commons.hpc]\ninstall = "x"\n').item_wall == ItemWall()
    assert ItemWall(multiple=5, floor_s=120).bound(100.0) == 500
    assert ItemWall(multiple=5, floor_s=120).bound(1.0) == 120
    assert ItemWall(unmeasured_s=900).bound(None) == 900
    with pytest.raises(ValueError, match='item_wall'):
        read_builds('[tool.lab_commons.hpc]\ninstall = "x"\nitem_wall = {ceiling = 3}\n')
    with pytest.raises(ValueError, match='item_wall'):
        read_builds('[tool.lab_commons.hpc]\ninstall = "x"\nitem_wall = {floor_s = 0}\n')
