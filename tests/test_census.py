"""The process census reads this very process back from the platform's own table."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lab_commons import _proc_darwin, census
from lab_commons.census import Process, _etime, processes


def _me() -> Process:
    rows = {row.pid: row for row in processes()}
    assert os.getpid() in rows, 'the census sees the process reading it'
    return rows[os.getpid()]


def test_the_census_reads_this_process_its_parent_command_line_and_age() -> None:
    me = _me()
    assert me.ppid == os.getppid()
    assert Path(sys.argv[0]).name in me.command_line or 'pytest' in me.command_line
    assert me.age_s is not None
    assert me.age_s >= 0.0


def test_the_census_reads_where_this_process_executes_from_and_runs_in() -> None:
    me = _me()
    assert Path(me.executable).name.lower().startswith('python')
    assert Path(me.cwd).resolve() == Path.cwd().resolve()


def test_an_etime_is_read_as_seconds_and_a_garbled_one_as_unknown() -> None:
    assert _etime('1-02:03:04') == 86400 + 2 * 3600 + 3 * 60 + 4
    assert _etime('03:04') == 184.0
    assert _etime('x') is None


_PS = (
    '  412     1 1-02:03:04 /Users/me/repo/.venv/bin/python -m dev_lab.dev.verify -- tests\n'
    '  413   412      00:05 .venv/bin/python -c pass\n'
    '  414   412      00:01 sleep 5\n'
    ' garbage\n'
)
_LSOF = 'p412\nfcwd\nn/Users/me/repo\np413\nfcwd\nn/Users/me/other dir\n'


def test_macos_reads_pid_parent_age_command_line_executable_and_cwd_from_ps_and_lsof(monkeypatch) -> None:
    tools = {'ps': _PS, 'lsof': _LSOF}
    monkeypatch.setattr(_proc_darwin, '_tool', lambda *argv: tools.get(argv[0]))
    rows = {row.pid: row for row in census._darwin()}
    assert rows[412] == Process(
        412,
        1,
        '/Users/me/repo/.venv/bin/python',
        '/Users/me/repo/.venv/bin/python -m dev_lab.dev.verify -- tests',
        86400 + 2 * 3600 + 3 * 60 + 4.0,
        '/Users/me/repo',
    )
    assert rows[413].executable == '/Users/me/other dir/.venv/bin/python', 'relative argv0 against cwd'
    assert rows[413].cwd == '/Users/me/other dir'
    assert (rows[414].executable, rows[414].cwd) == ('', ''), 'planted control: unread is empty, never guessed'
    assert set(rows) == {412, 413, 414}


def test_macos_without_ps_has_no_census(monkeypatch) -> None:
    monkeypatch.setattr(_proc_darwin, '_tool', lambda *_argv: None)
    assert census._darwin() == ()
