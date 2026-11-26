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


FAMILY = {
    'storage': {
        'remotes': {
            'results': {
                'type': 's3', 'endpoint': 'https://objects.example', 'secret_keys': ['secret_access_key'],
            }
        }
    }
}  # fmt: skip


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


def test_the_free_floor_defaults_to_fifty_gib_when_a_machine_declares_none() -> None:
    """PLANTED CONTROL for the declared floor below: an undeclared floor is the family default, not unbounded."""
    assert declared({}, {}).free_floor == storage.FREE_FLOOR_DEFAULT == 50 * 1024**3
    assert storage.free_floor(declared({}, {})) == Floor(50 * 1024**3)


def test_a_machine_declares_its_own_free_floor_as_a_size() -> None:
    small = declared({'storage': {'free_floor': '8 GiB'}}, {})
    assert small.free_floor == 8 * 1024**3
    assert storage.free_floor(small) == Floor(8 * 1024**3)


@pytest.mark.parametrize('bad', ['0 GiB', '-5 GiB', 'fifty', True, 50, '50', '50 min'])
def test_a_free_floor_that_is_not_a_positive_size_is_refused(bad: object) -> None:
    with pytest.raises(StorageError, match=r'\[storage\] free_floor = '):
        declared({'storage': {'free_floor': bad}}, {})


def test_the_retired_unit_suffixed_floor_is_refused_with_its_new_line() -> None:
    """FORMATS-MIGRATE-ONCE: the old key is never read; the refusal writes the replacement from its own value."""
    with pytest.raises(StorageError, match=r'free_floor_gb = 8 is retired.*write free_floor = "8 GiB"'):
        declared({'storage': {'free_floor_gb': 8}}, {})


def test_the_free_floor_is_a_machine_fact() -> None:
    with pytest.raises(StorageError, match='free_floor belongs to one machine'):
        declared({}, {'storage': {'free_floor': '50 GiB'}})


@pytest.mark.parametrize('retired', ['vendor_cache_cap_gb', 'cache_cap_gb', 'cap_gb'])
def test_a_retired_cache_cap_is_refused_by_name(retired: str) -> None:
    with pytest.raises(StorageError, match=f'{retired} is retired.*free_floor'):
        declared({'storage': {retired: 200}}, {})


EVERYONE = {
    'storage': {
        'remotes': {
            'results': {
                'type': 's3',
                'endpoint': 'https://objects.example',
                'reach': {'credentials_file': '/keys/results'},
                'access_key_id': '0123abcd',
                'secret_keys': ['secret_access_key'],
                'ask': {'secret_access_key': 'shown once when the key is generated'},
                'shape': {'secret_access_key': '[A-Za-z0-9+/=]{44}'},
            }
        }
    }
}


def test_a_family_reach_gives_every_machine_the_remote_and_its_file() -> None:
    """Stated once in the family; a machine declaring nothing reaches it. PLANTED: the meta never reaches rclone."""
    (remote,) = storage.declared({'storage': {}}, EVERYONE).reach
    assert remote.credentials_file == Path('/keys/results')
    assert remote.ask == {'secret_access_key': 'shown once when the key is generated'}
    assert remote.secret_keys == ('secret_access_key',)
    assert remote.options['access_key_id'] == '0123abcd'
    assert not {'reach', 'ask', 'shape', 'secret_keys'} & set(remote.options)
    assert remote.options['endpoint'] == 'https://objects.example'


def test_a_machine_restating_the_family_reach_is_refused() -> None:
    machine = {'storage': {'reach': {'results': {'credentials_file': '/keys/results'}}}}
    with pytest.raises(storage.StorageError, match='restates the family reach'):
        storage.declared(machine, EVERYONE)


def test_a_machine_keeps_the_key_elsewhere_by_declaring_its_own_file() -> None:
    machine = {'storage': {'reach': {'results': {'credentials_file': '/other/results'}}}}
    (remote,) = storage.declared(machine, EVERYONE).reach
    assert remote.credentials_file == Path('/other/results')


def _family(**remote: object) -> dict[str, object]:
    return {'storage': {'remotes': {'results': {**EVERYONE['storage']['remotes']['results'], **remote}}}}


@pytest.mark.parametrize(
    ('remote', 'why'),
    [
        ({'secret_keys': []}, 'declares no secret_keys'),
        ({'secret_keys': ['access_key_id']}, 'both as a fact and as a secret key'),
        ({'ask': {'access_key_id': 'the Access Key column'}}, 'access_key_id, which is not a secret key'),
        ({'shape': {'endpoint': '.*'}}, 'endpoint, which is not a secret key'),
    ],
)
def test_only_a_declared_secret_is_asked(remote: dict[str, object], why: str) -> None:
    """Asking a declared non-secret key (an S3 access key ID) is refused at the declaration.

    PLANTED CONTROL: the well-declared family above resolves (test_a_family_reach_gives_every_machine_...).
    """
    with pytest.raises(storage.StorageError, match=why):
        storage.declared({'storage': {}}, _family(**remote))


_RUNS_REMOTE = {'type': 's3', 'secret_keys': ['secret_access_key'], 'reach': {'credentials_file': '~/k'}}


def test_the_family_names_the_store_every_run_uses() -> None:
    held = declared({}, {'storage': {'remotes': {'results': _RUNS_REMOTE}, 'runs': 'results:bucket'}})
    assert (held.runs, held.runs_remote) == ('results:bucket', 'results')


def test_no_runs_declared_is_no_run_store() -> None:
    """PLANTED CONTROL: undeclared reads None, never a guessed remote."""
    held = declared({}, {'storage': {'remotes': {'results': _RUNS_REMOTE}}})
    assert (held.runs, held.runs_remote) == (None, None)


@pytest.mark.parametrize('bad', ['results', 'results:', ':bucket', 7])
def test_a_runs_value_not_remote_colon_path_is_refused(bad: object) -> None:
    with pytest.raises(StorageError, match=r'runs = .* must be "<remote>:<path>"'):
        declared({}, {'storage': {'remotes': {'results': _RUNS_REMOTE}, 'runs': bad}})


def test_runs_on_an_undeclared_remote_or_in_a_machine_file_is_refused() -> None:
    with pytest.raises(StorageError, match=r"runs names remote 'other'"):
        declared({}, {'storage': {'remotes': {'results': _RUNS_REMOTE}, 'runs': 'other:bucket'}})
    with pytest.raises(StorageError, match='runs is a family fact'):
        declared({'storage': {'runs': 'results:bucket'}}, {'storage': {'remotes': {'results': _RUNS_REMOTE}}})
