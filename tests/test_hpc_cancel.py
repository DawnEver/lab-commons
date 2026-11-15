"""``python -m lab_commons.hpc cancel``: the one door that cancels named or oversized pending Slurm jobs."""

from __future__ import annotations

from typing import Final

import pytest

from lab_commons.hpc import __main__ as entry
from lab_commons.hpc import cancel

AT: Final = 'me@login.example'

#: ``squeue`` rows as the door asks for them: id|state|mem|user|name (a name may hold the separator).
QUEUE: Final = (
    '101|PENDING|284G|me|big-pending\n'
    '102|RUNNING|300G|me|big-running\n'
    '103|PENDING|4000M|me|small|pending\n'
    '104|PENDING|500G|other|foreign-big\n'
    '105|PENDING|1T|me|huge\n'
)


class FakeQueue:
    """A runner that answers ``squeue`` with *QUEUE* and records every command."""

    def __init__(self, queue: str = QUEUE) -> None:
        """Answer ``squeue`` with *queue*."""
        self.queue = queue
        self.commands: list[str] = []

    def __call__(self, command: str, stdin: str | None = None) -> str:
        self.commands.append(command)
        if command.startswith('squeue'):
            return self.queue
        if command.startswith('scancel'):
            return ''
        raise AssertionError(command)

    def cancelled(self) -> list[str]:
        return [c for c in self.commands if c.startswith('scancel')]


def _cancel(argv: list[str], queue: FakeQueue) -> int:
    return cancel.main(['--at', AT, *argv], at=lambda _target, _timeout: queue)


@pytest.mark.parametrize(
    ('text', 'gb'), [('284G', 284.0), ('4000M', 4000 / 1024), ('1T', 1024.0), ('512K', 512 / 1024**2), ('7', 7 / 1024)]
)
def test_a_slurm_memory_reads_as_gigabytes(text: str, gb: float) -> None:
    assert cancel.gigabytes(text) == pytest.approx(gb, rel=1e-9, abs=1e-12)


def test_a_job_id_selects_only_that_own_job() -> None:
    queue = FakeQueue()
    assert _cancel(['--job', '101'], queue) == 0
    assert queue.cancelled() == ['scancel -u me 101']


def test_pending_over_mem_never_selects_a_running_small_or_foreign_job() -> None:
    queue = FakeQueue()
    assert _cancel(['--pending-over-mem', '100'], queue) == 0
    assert queue.cancelled() == ['scancel -u me 101 105']


def test_a_foreign_job_id_is_never_cancelled(capsys: pytest.CaptureFixture[str]) -> None:
    queue = FakeQueue()
    assert _cancel(['--job', '104'], queue) == 1
    assert queue.cancelled() == []
    assert '104' in capsys.readouterr().err


def test_dry_run_lists_and_never_calls_scancel(capsys: pytest.CaptureFixture[str]) -> None:
    queue = FakeQueue()
    assert _cancel(['--pending-over-mem', '100', '--dry-run'], queue) == 0
    assert queue.cancelled() == []
    out = capsys.readouterr().out
    assert 'would cancel 101 PENDING 284G big-pending' in out
    assert '102' not in out


def test_no_selector_is_refused_before_any_command() -> None:
    queue = FakeQueue()
    assert _cancel([], queue) == 2
    assert queue.commands == []


def test_a_non_numeric_job_id_is_refused() -> None:
    queue = FakeQueue()
    assert _cancel(['--job', '1;rm'], queue) == 2
    assert queue.commands == []


def test_an_array_job_is_selected_by_its_base_id() -> None:
    queue = FakeQueue('200_[1-8]|PENDING|300G|me|arr\n301_3|RUNNING|300G|me|arr\n')
    assert _cancel(['--job', '200'], queue) == 0
    assert queue.cancelled() == ["scancel -u me '200_[1-8]'"]


def test_the_queue_is_read_for_that_user_only() -> None:
    queue = FakeQueue()
    _cancel(['--job', '101', '--dry-run'], queue)
    assert queue.commands == ["squeue -u me -h -o '%i|%T|%m|%u|%j'"]


def test_the_verb_is_a_door_of_the_hpc_entry_point() -> None:
    assert entry._dispatch(['cancel']) == 2  # reached the door: refused for want of a selector, nothing run
