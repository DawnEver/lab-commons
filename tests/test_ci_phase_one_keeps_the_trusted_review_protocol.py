"""Finite phase one: the project kit is new, but the trusted base still owns the review protocol."""

import copy
import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from dev_lab.dev import ciworkflows

_ROOT = Path(__file__).resolve().parents[1]
_OVERRIDE = '[tool.dev_lab.ci._steps]'
_BASE_METHOD_HASH = '744f3bbda5875a3ddd54b9e221246759aacf1fd8742b55a67806b3c2c94fe2dc'
_BASE_REVIEW_HASH = 'd1f394c5fbc58fc8a377d3aa88d532c3db21a66e712bc69498e91efaf810f4a9'
_METHOD_HASH = '09a95c74cb1596f4e9c16ca189111f6b1e39c8206610841f5a1f2832053398cb'
_REVIEW_HASH = '41bf69b27cae24d124e5b7c20be677e8f1263b2b6c6ae9e81cfa7386206181f4'


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _canonical() -> dict:
    shared = tomllib.loads(ciworkflows.BASE_PATH.read_text(encoding='utf-8'))['_steps']
    assert _digest(shared['method']) == _BASE_METHOD_HASH
    assert _digest(shared['review']) == _BASE_REVIEW_HASH
    return shared


def _declared() -> dict:
    return tomllib.loads((_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['dev_lab']['ci']['_steps']


def _check(phase: dict) -> None:
    assert set(phase) == {'method', 'review'}
    assert _digest(phase['method']) == _METHOD_HASH
    assert _digest(phase['review']) == _REVIEW_HASH
    assert phase['method'][0] == _canonical()['method'][0]


def test_new_project_kit_and_finite_phase_one_have_their_exact_canonical_owners() -> None:
    _check(_declared())


@pytest.mark.parametrize('field', ['installer', 'method_auth', 'review_cli', 'review_env'])
def test_phase_one_refuses_installer_auth_and_review_protocol_drift(field: str) -> None:
    phase = copy.deepcopy(_declared())
    if field == 'installer':
        phase['method'][0]['run'] += '\nexit 0\n'
    elif field == 'method_auth':
        phase['method'][1]['run'] = 'true'
    elif field == 'review_cli':
        phase['review'][0]['run'] = 'true'
    else:
        phase['review'][0]['env'] = {}
    with pytest.raises(AssertionError):
        _check(phase)


def _expected_workflows(tmp_path: Path) -> dict:
    text = (_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert text.count(_OVERRIDE) == 1
    (tmp_path / 'pyproject.toml').write_text(text.split(_OVERRIDE)[0], encoding='utf-8')
    expected = copy.deepcopy(ciworkflows.workflows(tmp_path))
    canonical, phase = _canonical(), _declared()
    replacements = (
        (canonical['method'][1], phase['method'][1]),
        (canonical['review'][0], phase['review'][0]),
    )
    counts = [0, 0]
    for workflow in expected.values():
        for job in workflow['jobs'].values():
            for index, step in enumerate(job.get('steps', [])):
                for position, (original, replacement) in enumerate(replacements):
                    if step.get('name') == original['name'] and step.get('run') == original['run']:
                        changed = copy.deepcopy(replacement)
                        if 'if' in step:
                            changed['if'] = step['if']
                        job['steps'][index] = changed
                        counts[position] += 1
    assert all(count > 0 for count in counts)
    return expected


def _check_workflows(actual: dict, tmp_path: Path) -> None:
    assert actual == _expected_workflows(tmp_path)


def test_phase_one_keeps_every_other_canonical_workflow_field_and_trusted_base_door(tmp_path: Path) -> None:
    actual = ciworkflows.workflows(_ROOT)
    assert {'ci.yml', 'merge-tick.yml', 'review-again.yml'} <= set(actual)
    _check_workflows(actual, tmp_path)
    doors = [
        step
        for workflow in actual.values()
        for job in workflow['jobs'].values()
        for step in job.get('steps', [])
        if step.get('name') == 'Install the review door'
    ]
    assert doors
    for step in doors:
        assert step['env']['DOOR_SHA'] == '${{ github.event.pull_request.base.sha || env.INTEGRATION_SHA }}'
        assert 'HEAD_SHA' not in step['run']


def test_a_planted_pr_head_door_cannot_hide_in_the_phase_one_workflow(tmp_path: Path) -> None:
    expected = _expected_workflows(tmp_path)
    changed = copy.deepcopy(expected)
    step = next(
        step for step in changed['ci.yml']['jobs']['review']['steps'] if step.get('name') == 'Install the review door'
    )
    step['env']['DOOR_SHA'] = '${{ github.event.pull_request.head.sha }}'
    with pytest.raises(AssertionError):
        _check_workflows(changed, tmp_path)
