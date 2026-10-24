"""D16: a unit's hash is engine + version + entry + input CONTENT + canonical parameters, and nothing else."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lab_commons.compute.model import Demand
from lab_commons.compute.presence import Evidence, Presence
from lab_commons.compute.unit import (
    Engine,
    EngineAbsent,
    Environment,
    Output,
    Unit,
    content_hash,
    environment_here,
)

_GIT = shutil.which('git') or 'git'
SMALL = Demand(cpu=1, mem_gb=0.1)
ENV = Environment('a' * 40, 'k' * 24)


def unit(inputs: dict[str, object], *, engine: Engine | None = None, entry: str = 'pkg.mod:fn') -> Unit:
    return Unit(engine or Engine.ours('ours.solve', ENV), entry, inputs, ('out.txt',), SMALL, 60)


def test_the_hash_reads_file_content_never_its_path(tmp_path: Path) -> None:
    one, two = tmp_path / 'a.txt', tmp_path / 'b.txt'
    one.write_text('mesh', encoding='utf-8')
    two.write_text('mesh', encoding='utf-8')
    u = unit({'mesh': one})
    assert u.unit_hash({'mesh': one}) == u.unit_hash({'mesh': two})
    two.write_text('other', encoding='utf-8')
    assert u.unit_hash({'mesh': one}) != u.unit_hash({'mesh': two})  # PLANTED: content moved, so does the key


def test_parameters_are_canonical_json() -> None:
    assert unit({'a': 1, 'b': [1, 2]}).unit_hash({}) == unit({'b': [1, 2], 'a': 1}).unit_hash({})
    assert unit({'a': 1}).unit_hash({}) != unit({'a': 2}).unit_hash({})
    with pytest.raises(ValueError, match=r'Out of range'):
        unit({'a': float('nan')})


def test_engine_version_and_entry_are_in_the_key() -> None:
    base = unit({'a': 1}).unit_hash({})
    assert unit({'a': 1}, engine=Engine.ours('ours.solve', Environment('b' * 40, 'k' * 24))).unit_hash({}) != base
    assert unit({'a': 1}, engine=Engine('femm', '5.0')).unit_hash({}) != unit(
        {'a': 1}, engine=Engine('femm', '5.1')
    ).unit_hash({})
    assert unit({'a': 1}, entry='pkg.mod:other').unit_hash({}) != base


def test_the_hash_needs_every_file_input_resolved() -> None:
    u = unit({'mesh': Output('mesher', 'mesh.msh'), 'n': 3})
    assert u.upstream == {'mesher'}
    with pytest.raises(ValueError, match='file inputs'):
        u.unit_hash({})


def test_a_directory_hashes_by_its_files(tmp_path: Path) -> None:
    (tmp_path / 'd').mkdir()
    (tmp_path / 'd' / 'x').write_text('1', encoding='utf-8')
    first = content_hash(tmp_path / 'd')
    (tmp_path / 'd' / 'x').write_text('2', encoding='utf-8')
    assert content_hash(tmp_path / 'd') != first


def test_a_vendor_version_is_the_probed_one_and_absence_refuses() -> None:
    probed = {'femm': Presence('femm', present=True, evidence=(Evidence('version: file', '5.0', 't'),))}
    assert Engine.vendor('femm', probed) == Engine('femm', '5.0')
    gone = {'femm': Presence('femm', present=False, evidence=(Evidence('find', 'not found', 't'),))}
    with pytest.raises(EngineAbsent, match='not found'):
        Engine.vendor('femm', gone)
    with pytest.raises(EngineAbsent, match='never probed'):
        Engine.vendor('jmag', probed)


def test_a_dirty_tree_is_local_only(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run([_GIT, '-C', str(tmp_path), *args], check=True, capture_output=True, timeout=60)

    git('init', '-q')
    git('config', 'user.email', 'x@example.invalid')
    git('config', 'user.name', 'x')
    (tmp_path / 'pyproject.toml').write_text('[tool.lab_commons.hpc]\ninstall = "uv sync"\n', encoding='utf-8')
    git('add', 'pyproject.toml')
    git('commit', '-q', '-m', 'init')
    clean = environment_here(tmp_path)
    assert not clean.local_only
    assert not Engine.ours('ours.solve', clean).local_only
    (tmp_path / 'new.py').write_text('x = 1\n', encoding='utf-8')
    dirty = environment_here(tmp_path)
    assert dirty.local_only
    assert (dirty.sha, dirty.lock_hash) == (clean.sha, clean.lock_hash)
    assert dirty.key.startswith(clean.key + '+')
    assert Engine.ours('ours.solve', dirty).local_only
