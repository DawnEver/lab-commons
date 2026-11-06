"""Temporary bridge; delete with the override when the new kit becomes the trusted base."""

import copy
import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from dev_lab.dev import ciworkflows

_ROOT = Path(__file__).resolve().parents[1]
_OVERRIDE = '[tool.dev_lab.ci._steps]'
_OLD_HASH = 'cd0d935b6de0617cd6fa06e91d89d7cd5c843f8b56dba2e04239861fbce85ba4'
_DERIVED_HASH = '09a95c74cb1596f4e9c16ca189111f6b1e39c8206610841f5a1f2832053398cb'
_RUN_HASH = '71e30b863c80a7e274f31ad18b8873a46076a794f45276ebf34dded066fe6f9f'


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _old_method() -> list[dict]:
    method = tomllib.loads(ciworkflows.BASE_PATH.read_text(encoding='utf-8'))['_steps']['method']
    assert _digest(method) == _OLD_HASH, 'the bridge must retire when the locked renderer advances'
    return method


def _check(method: list[dict]) -> None:
    assert _digest(method) == _DERIVED_HASH
    assert hashlib.sha256(method[0]['run'].encode()).hexdigest() == _RUN_HASH
    restored = copy.deepcopy(method)
    restored[0]['run'] = _old_method()[0]['run']
    assert restored == _old_method()


def _declared_method() -> list[dict]:
    data = tomllib.loads((_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    return data['tool']['dev_lab']['ci']['_steps']['method']


def test_only_the_installer_run_is_derived_from_the_new_canonical_owner() -> None:
    _check(_declared_method())


@pytest.mark.parametrize('field', ['installer', 'name', 'env', 'auth'])
def test_the_bootstrap_refuses_changes_beyond_its_one_installer(field: str) -> None:
    method = copy.deepcopy(_declared_method())
    if field == 'installer':
        method[0]['run'] += '\nexit 0\n'
    elif field == 'name':
        method[0]['name'] = 'different installer'
    elif field == 'env':
        method[0]['env'] = {}
    else:
        method[1]['run'] = 'true'
    with pytest.raises(AssertionError):
        _check(method)


def test_the_rendered_door_cli_keys_and_every_other_workflow_field_remain_unchanged(tmp_path: Path) -> None:
    text = (_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert text.count(_OVERRIDE) == 1
    (tmp_path / 'pyproject.toml').write_text(text.split(_OVERRIDE)[0], encoding='utf-8')
    old = ciworkflows.workflows(tmp_path)
    expected = copy.deepcopy(old)
    count = 0
    for workflow in expected.values():
        for job in workflow['jobs'].values():
            for step in job.get('steps', []):
                if step.get('name') == _old_method()[0]['name']:
                    assert step['run'] == _old_method()[0]['run']
                    step['run'] = _declared_method()[0]['run']
                    count += 1
    assert count == 1
    assert ciworkflows.workflows(_ROOT) == expected
