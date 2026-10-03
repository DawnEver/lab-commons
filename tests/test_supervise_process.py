"""The process-manager seam: what a manager would execute, and what it refuses to pretend."""

from __future__ import annotations

import sys

import pytest

from lab_commons.supervise.process import MANAGERS, SystemdProcessManager, manager_for, subprocess_runner
from lab_commons.supervise.process.base import Ran
from lab_commons.supervise.process.systemd import Runner


def _recorder(calls: list[tuple[list[str], int, str | None]], code: int = 0) -> Runner:
    """Return a runner that records what it was asked to run instead of running it."""

    def run(argv: list[str], timeout: int, cwd: str | None) -> Ran:
        calls.append((argv, timeout, cwd))
        return Ran(code=code, out='active')

    return run


def test_a_manager_is_looked_up_by_the_name_a_config_uses() -> None:
    """The table is explicit, so a name that is not in it is a refusal rather than a guess."""
    manager = manager_for('systemd')
    assert isinstance(manager, SystemdProcessManager)
    assert manager.name == 'systemd'


def test_an_unknown_manager_name_names_what_does_exist() -> None:
    """A refusal that does not say what would have worked sends a reader to the docs."""
    with pytest.raises(ValueError, match='systemd'):
        manager_for('supervisord')


def test_the_table_holds_the_builtin() -> None:
    """Guards against the table silently losing its only entry."""
    assert set(MANAGERS) == {'systemd'}


def test_is_active_reads_the_exit_status() -> None:
    """`systemctl is-active` answers in its status, not in its text."""
    calls: list[tuple[list[str], int, str | None]] = []
    manager = SystemdProcessManager(runner=_recorder(calls, code=0))
    assert manager.is_active('webapp') is True
    assert calls[0][0] == ['systemctl', 'is-active', 'webapp']
    assert SystemdProcessManager(runner=_recorder(calls, code=3)).is_active('webapp') is False


@pytest.mark.parametrize('verb', ['start', 'stop', 'restart'])
def test_lifecycle_verbs_are_one_command_each(verb: str) -> None:
    """Hosting the process is the manager's job, so the supervisor only names the verb."""
    calls: list[tuple[list[str], int, str | None]] = []
    manager = SystemdProcessManager(runner=_recorder(calls))
    if verb == 'start':
        result = manager.start('webapp')
    elif verb == 'stop':
        result = manager.stop('webapp')
    else:
        result = manager.restart('webapp')
    assert result.ok
    assert calls[0][0] == ['systemctl', verb, 'webapp']


def test_a_capped_run_carries_its_ceiling_and_kills_inside_the_scope() -> None:
    """The ceiling is the reason this method exists; the timeout goes INSIDE the scope."""
    calls: list[tuple[list[str], int, str | None]] = []
    manager = SystemdProcessManager(runner=_recorder(calls))
    manager.run_capped(['python', '-c', 'import x'], memory_max='600M', timeout=120)
    argv, allowed, _cwd = calls[0]
    assert argv[:2] == ['systemd-run', '--scope']
    assert '--property=MemoryMax=600M' in argv
    marker = argv.index('--')
    assert argv[marker + 1 : marker + 3] == ['timeout', '120']
    assert argv[-3:] == ['python', '-c', 'import x']
    assert allowed > 120, 'the wrapper must outlive the timeout it hands to the scope'


def test_a_failing_command_is_a_ran_not_an_exception() -> None:
    """A refusal is an outcome to report; unwinding would take the cycle down with it."""
    result = subprocess_runner([sys.executable, '-c', 'raise SystemExit(7)'], 30, None)
    assert result.ok is False
    assert result.code == 7


def test_a_command_that_cannot_be_run_reports_rather_than_raises() -> None:
    """A missing binary is the ordinary case on a host that is not the deployment target."""
    result = subprocess_runner(['definitely-not-a-binary-xyz'], 30, None)
    assert result.ok is False
    assert 'could not run' in result.err


def test_a_silent_failure_still_says_something() -> None:
    """A log line reading 'exit 1 with no output' beats an empty one."""
    assert Ran(code=1).detail() == 'exit 1 with no output'


def test_detail_prefers_the_stream_that_explains() -> None:
    """Standard error is where a refusal explains itself, so it is read first."""
    assert Ran(code=1, out='noise', err='the real reason').detail() == 'the real reason'
