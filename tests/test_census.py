"""The process census reads this very process back from the platform's own table."""

from __future__ import annotations

import os
import sys
from pathlib import Path

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
    if sys.platform == 'darwin':
        return  # macOS reads neither: an unreadable field is '', never a guess
    me = _me()
    assert Path(me.executable).name.lower().startswith('python')
    assert Path(me.cwd).resolve() == Path.cwd().resolve()


def test_an_etime_is_read_as_seconds_and_a_garbled_one_as_unknown() -> None:
    assert _etime('1-02:03:04') == 86400 + 2 * 3600 + 3 * 60 + 4
    assert _etime('03:04') == 184.0
    assert _etime('x') is None
