"""``lab_commons.storage``: one resolver for where a repo's bulky state lives on a machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons import paths, storage
from lab_commons.headroom import Floor
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


FAMILY = {'storage': {'remotes': {'results': {'type': 's3', 'endpoint': 'https://objects.example'}}}}


def test_a_reached_remote_comes_from_the_family_with_its_own_credentials_file(tmp_path: Path) -> None:
    file = tmp_path / 'results'
    got = declared({'storage': {'reach': {'results': {'credentials_file': str(file)}}}}, FAMILY)
    assert [(r.name, dict(r.options), r.credentials_file) for r in got.reach] == [
        ('results', {'type': 's3', 'endpoint': 'https://objects.example'}, file)
    ]


def test_a_credentials_file_expands_the_home() -> None:
    got = declared({'storage': {'reach': {'results': {'credentials_file': '~/.motronics/secrets/results'}}}}, FAMILY)
    assert got.reach[0].credentials_file == Path('~/.motronics/secrets/results').expanduser()


def test_no_reach_declared_reaches_nothing() -> None:
    """PLANTED CONTROL for the refusals below: a machine declaring no reach reaches no remote and needs no file."""
    assert declared({}, FAMILY).reach == ()


def test_a_remote_the_family_does_not_declare_is_refused() -> None:
    with pytest.raises(StorageError, match="names 'results', which the family file declares no remote"):
        declared({'storage': {'reach': {'results': {'credentials_file': '/s/results'}}}}, {})


def test_a_reached_remote_without_a_credentials_file_is_refused_by_name() -> None:
    with pytest.raises(StorageError, match=r'\[storage.reach.results\] declares no credentials_file'):
        declared({'storage': {'reach': {'results': {}}}}, FAMILY)


def test_an_unknown_reach_key_is_refused() -> None:
    with pytest.raises(StorageError, match='secret_dir is not a reach key'):
        declared({'storage': {'reach': {'results': {'credentials_file': '/s', 'secret_dir': '/d'}}}}, FAMILY)


def test_the_retired_secrets_directory_is_refused_by_name_with_the_new_shape() -> None:
    """FORMATS-MIGRATE-ONCE: the old directory spelling is never read, only refused, naming what replaced it."""
    with pytest.raises(StorageError, match=r'secrets is retired: .*\[storage.reach.<remote>\] credentials_file'):
        declared({'storage': {'reach': {'results': {'credentials_file': '/s'}}, 'secrets': '/old'}}, FAMILY)


def test_the_retired_list_form_reach_is_refused_by_name_with_the_new_shape() -> None:
    with pytest.raises(StorageError, match=r'reach as a list is retired: .*credentials_file'):
        declared({'storage': {'reach': ['results']}}, FAMILY)


def test_a_rendered_machine_file_carries_the_family_remotes_inline(tmp_path: Path) -> None:
    """No family layer is declared: the machine file IS the render of family + host, so its remotes are read."""
    file = tmp_path / 'results'
    rendered = {'storage': {**FAMILY['storage'], 'reach': {'results': {'credentials_file': str(file)}}}}
    got = declared(rendered, {})
    assert [(r.name, r.credentials_file) for r in got.reach] == [('results', file)]


def test_each_key_lives_in_its_own_layer() -> None:
    """Planted control: with a family layer present, remotes in the machine file restate it and are refused."""
    with pytest.raises(StorageError, match='remotes is a family fact'):
        declared({'storage': {'remotes': {}}}, FAMILY)
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
    assert storage.free_floor(declared({}, {})) == Floor(50 * 1024**3)


def test_a_machine_declares_its_own_free_floor() -> None:
    small = declared({'storage': {'free_floor_gb': 8}}, {})
    assert small.free_floor_gb == 8
    assert storage.free_floor(small) == Floor(8 * 1024**3)


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


EVERYONE = {
    'storage': {
        'remotes': {
            'results': {
                'type': 's3',
                'endpoint': 'https://objects.example',
                'reach': {'credentials_file': '/keys/results'},
                'ask': {'access_key_id': 'the Access Key column'},
                'shape': {'access_key_id': '[0-9a-f]{40}'},
            }
        }
    }
}


def test_a_family_reach_gives_every_machine_the_remote_and_its_file() -> None:
    """Stated once in the family; a machine declaring nothing reaches it. PLANTED: the meta never reaches rclone."""
    (remote,) = storage.declared({'storage': {}}, EVERYONE).reach
    assert remote.credentials_file == Path('/keys/results')
    assert remote.ask == {'access_key_id': 'the Access Key column'}
    assert 'reach' not in remote.options
    assert 'ask' not in remote.options
    assert remote.options['endpoint'] == 'https://objects.example'


def test_a_machine_restating_the_family_reach_is_refused() -> None:
    machine = {'storage': {'reach': {'results': {'credentials_file': '/keys/results'}}}}
    with pytest.raises(storage.StorageError, match='restates the family reach'):
        storage.declared(machine, EVERYONE)


def test_a_machine_keeps_the_key_elsewhere_by_declaring_its_own_file() -> None:
    machine = {'storage': {'reach': {'results': {'credentials_file': '/other/results'}}}}
    (remote,) = storage.declared(machine, EVERYONE).reach
    assert remote.credentials_file == Path('/other/results')
