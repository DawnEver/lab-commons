"""``box.run`` with real processes: the enforcer holds the tree to exactly its claim, or the run is refused.

NO SKIPS, ON PURPOSE. Each test states the contract on the machine it runs on: where an enforcer
exists (a Windows Job Object, a delegated systemd scope) the limit is observed killing; where none
does (Linux without a user cgroup, macOS) the observed fact is the refusal -- never an unenforced run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from lab_commons.box import BOX_ENV, GB, Capacity, Demand, Outcome, Refused, holdings, run
from lab_commons.box.__main__ import KILLED, REFUSED, main
from lab_commons.box._linux import refusal
from lab_commons.proc import pid_alive

#: Does this machine enforce a claim? A platform fact, read once.
ENFORCES = sys.platform == 'win32' or (sys.platform.startswith('linux') and refusal() is None)

_CAP = Capacity(cpu=2.0, mem_bytes=4 * GB, seats={})
_MB = 2**20
#: 400 MB, touched page by page so a lazily-committing kernel (Linux) accounts it, then HELD: a watched
#: (Slurm) run is sampled per tick, and a hog that held nothing would not be the case under test.
_HOG = 'import time; b = bytearray(400 * 2**20); b[::4096] = b"x" * len(b[::4096]); time.sleep(30)'


@pytest.fixture(autouse=True)
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    return path


def _run(code: str, *, mem_mb: int = 200, wall_s: int = 30) -> Outcome | None:
    """The enforced Outcome, or ``None`` once the refusal this machine owes has been observed."""
    demand = Demand(cpu=1, mem_gb=mem_mb * _MB / GB, wall_s=wall_s, seats={})
    argv = [sys.executable, '-c', code]
    if not ENFORCES:
        with pytest.raises(Refused, match='unenforced'):
            run(argv, demand, 'refused', queue_s=5, capacity=_CAP, key='k')
        assert holdings() == []
        return None
    return run(argv, demand, 'enforced', queue_s=5, capacity=_CAP, key='solve|ipm|fea', fingerprint='abc')


def _gone(pid: int, within_s: float = 5.0) -> bool:
    end = time.monotonic() + within_s
    while pid_alive(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def _read_pid(path: Path, within_s: float = 20.0) -> int:
    end = time.monotonic() + within_s
    while not path.exists() or not path.read_text(encoding='utf-8').strip():
        assert time.monotonic() < end, f'{path} never written'
        time.sleep(0.05)
    return int(path.read_text(encoding='utf-8'))


def test_a_normal_child_returns_its_code_a_peak_and_leaves_no_row() -> None:
    out = _run('import sys; sys.exit(3)')
    if out is not None:
        assert (out.returncode, out.reason, out.key, out.fingerprint) == (3, 'exit', 'solve|ipm|fea', 'abc')
        assert out.peak_mem_bytes > 0
        assert holdings() == []


def test_a_child_allocating_past_its_cap_is_killed_for_memory() -> None:
    out = _run(_HOG, mem_mb=200)
    if out is not None:
        assert out.reason == 'memory'
        assert out.returncode != 0


def test_a_sleeping_child_is_killed_at_its_wall() -> None:
    out = _run('import time; time.sleep(60)', wall_s=2)
    if out is not None:
        assert out.reason == 'wall'
        assert 2.0 <= out.wall_s < 10.0


def test_a_grandchild_dies_with_its_claim(tmp_path: Path) -> None:
    pid_file = tmp_path / 'grandchild.pid'
    spawn = (
        'import subprocess, sys; '
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f'open({str(pid_file)!r}, "w").write(str(p.pid))'
    )
    out = _run(spawn)
    if out is not None:
        assert out.reason == 'exit'
        assert _gone(_read_pid(pid_file))


@pytest.mark.xfail(
    sys.platform != 'win32',
    reason='only a Job Object (KILL_ON_JOB_CLOSE) ties the tree to its launcher; a systemd scope or a watched Slurm '
    'group outlives it (the launcher owns the wall), and an unenforcing machine refuses before any child exists',
    strict=True,
)
def test_killing_the_launcher_kills_the_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / 'child.pid'
    child = f'import os, time; open({str(pid_file)!r}, "w").write(str(os.getpid())); time.sleep(60)'
    launcher = (
        'from lab_commons.box import Capacity, Demand, run; import sys; '
        f'run([sys.executable, "-c", {child!r}], Demand(cpu=1, mem_gb=0.2, wall_s=60, seats={{}}), '
        f'"orphan", queue_s=5, capacity=Capacity(cpu=2.0, mem_bytes={4 * GB}, seats={{}}), key="orphan")'
    )
    proc = subprocess.Popen([sys.executable, '-c', launcher])
    try:
        pid = _read_pid(pid_file, within_s=10.0)
        assert pid_alive(pid)
    finally:
        proc.kill()
        proc.wait()
    assert _gone(pid)


def test_the_cli_exits_killed_for_a_walled_child_and_refused_where_nothing_enforces() -> None:
    argv = ['run', '--cpu', '1', '--mem-gb', '0.2', '--wall', '2', '--queue-s', '5', '--key', 'cli', '--']
    code = main([*argv, sys.executable, '-c', 'import time; time.sleep(60)'])
    assert code == (KILLED if ENFORCES else REFUSED)
    assert holdings() == []
