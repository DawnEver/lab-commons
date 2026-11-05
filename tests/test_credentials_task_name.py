"""A task is a NAME here, never a declared list: lab-commons holds no task set (user ruling 2026-11-05)."""

from __future__ import annotations

import pytest

from lab_commons import credentials
from lab_commons.credentials import key_username


@pytest.mark.parametrize('task', ['pr-review', 'live', 'session', 'a-new-task-nobody-declared'])
def test_any_well_spelled_task_has_a_username(task: str) -> None:
    assert key_username(task) == f'api-key-{task}'
    assert key_username(task, 3) == f'api-key-{task}-3'


@pytest.mark.parametrize('task', ['2', 'x-2', 'PR-review', 'pr.review', '', '-x', 'x-'])
def test_a_task_that_could_read_as_a_slot_or_is_misspelt_is_refused(task: str) -> None:
    with pytest.raises(ValueError, match='not a task name'):
        key_username(task)


def test_the_module_declares_no_task_set() -> None:
    assert not any('TASKS' in name for name in vars(credentials))


def test_an_erased_slot_is_gone_and_its_neighbour_stays(tmp_path, monkeypatch) -> None:
    store = tmp_path / 'git-credentials'
    config = tmp_path / 'gitconfig'
    config.write_text(f'[credential]\n\thelper = "store --file={store.as_posix()}"\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(tmp_path / 'absent'))
    host = 'api.example.test'
    credentials.store_credential(host, key_username('live'), 'fake-one')
    credentials.store_credential(host, key_username('live', 2), 'fake-two')
    credentials.erase_credential(host, key_username('live', 2))
    assert credentials.api_keys(host, task='live') == ('fake-one',)
    credentials.erase_credential(host, key_username('live', 2))  # absent: already done
