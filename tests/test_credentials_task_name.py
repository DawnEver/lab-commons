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
