"""``lab_commons.storage``: one resolver for where a repo's bulky state lives on a machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons import paths, storage
from lab_commons.storage import KINDS, StorageError, declared, root

TREE = Path('/checkout/repo-a')


def _bound(**roots: str) -> storage.Storage:
    return declared({'storage': {'roots': roots}}, {})


def test_an_unbound_kind_is_its_in_tree_default() -> None:
    """PLANTED CONTROL for the binding below: with nothing declared, every kind stays in the checkout."""
    empty = declared({}, {})
    for kind, default in KINDS.items():
        expected = None if default is None else TREE / default
        assert root(kind, 'repo-a', TREE, storage=empty) == expected


def test_a_bound_kind_lands_under_its_machine_root_with_the_repo_filled(tmp_path: Path) -> None:
    bound = _bound(output=str(tmp_path / '{repo}' / 'out'))
    assert root('output', 'repo-a', TREE, storage=bound) == tmp_path / 'repo-a' / 'out'
    assert root('venvs', 'repo-a', TREE, storage=bound) == TREE / '.venvs', 'an unbound kind is unaffected'


def test_an_unknown_kind_is_refused_by_name() -> None:
    with pytest.raises(StorageError, match="'outputs' is not a storage kind"):
        root('outputs', 'repo-a', TREE, storage=declared({}, {}))
    with pytest.raises(StorageError, match=r'\[storage.roots\] cache is not a kind'):
        _bound(cache='/x')


def test_a_relative_root_is_refused() -> None:
    with pytest.raises(StorageError, match='is relative'):
        _bound(output='work/{repo}')


def test_a_reached_remote_comes_from_the_family_with_its_options_verbatim(tmp_path: Path) -> None:
    family = {'storage': {'remotes': {'results': {'type': 's3', 'endpoint': 'https://objects.example'}}}}
    got = declared({'storage': {'reach': ['results'], 'secrets': str(tmp_path)}}, family)
    assert [(r.name, dict(r.options)) for r in got.reach] == [
        ('results', {'type': 's3', 'endpoint': 'https://objects.example'})
    ]
    assert got.secrets == tmp_path


def test_a_remote_the_family_does_not_declare_is_refused() -> None:
    with pytest.raises(StorageError, match="reach names 'results'"):
        declared({'storage': {'reach': ['results'], 'secrets': '/s'}}, {})


def test_a_reach_without_a_secrets_path_is_refused() -> None:
    family = {'storage': {'remotes': {'results': {'type': 's3'}}}}
    with pytest.raises(StorageError, match='declares no secrets path'):
        declared({'storage': {'reach': ['results']}}, family)


def test_each_key_lives_in_its_own_layer() -> None:
    with pytest.raises(StorageError, match='remotes is a family fact'):
        declared({'storage': {'remotes': {}}}, {})
    with pytest.raises(StorageError, match='roots belongs to one machine'):
        declared({}, {'storage': {'roots': {}}})
    with pytest.raises(StorageError, match='mirror is not a storage key'):
        declared({'storage': {'mirror': 1}}, {})


def test_output_root_derives_from_the_storage_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``paths.output_root`` is no second spelling of the output kind: a binding moves it."""
    home = tmp_path / 'home'
    monkeypatch.setenv('APPX_HOME', str(home))
    monkeypatch.setattr(storage, 'declared', lambda: _bound(output=str(tmp_path / 'big' / '{repo}')))
    assert paths.output_root('appx') == tmp_path / 'big' / 'appx'
    monkeypatch.setattr(storage, 'declared', lambda: declared({}, {}))
    assert paths.output_root('appx') == home.resolve() / 'output'


def test_the_free_floor_defaults_to_fifty_gb_when_a_machine_declares_none() -> None:
    """PLANTED CONTROL for the declared floor below: an undeclared floor is the family default, not unbounded."""
    assert declared({}, {}).free_floor_gb == storage.FREE_FLOOR_GB_DEFAULT == 50
    assert storage.free_floor_bytes(declared({}, {})) == 50 * 1024**3


def test_a_machine_declares_its_own_free_floor() -> None:
    small = declared({'storage': {'free_floor_gb': 8}}, {})
    assert small.free_floor_gb == 8
    assert storage.free_floor_bytes(small) == 8 * 1024**3


@pytest.mark.parametrize('bad', [0, -5, 'fifty', True])
def test_a_free_floor_that_is_not_a_positive_number_is_refused(bad: object) -> None:
    with pytest.raises(StorageError, match=r'\[storage\] free_floor_gb'):
        declared({'storage': {'free_floor_gb': bad}}, {})


def test_the_free_floor_is_a_machine_fact() -> None:
    with pytest.raises(StorageError, match='free_floor_gb belongs to one machine'):
        declared({}, {'storage': {'free_floor_gb': 50}})


@pytest.mark.parametrize('retired', ['vendor_cache_cap_gb', 'cache_cap_gb', 'cap_gb'])
def test_a_retired_cache_cap_is_refused_by_name(retired: str) -> None:
    with pytest.raises(StorageError, match=f'{retired} is retired.*free_floor_gb'):
        declared({'storage': {retired: 200}}, {})
