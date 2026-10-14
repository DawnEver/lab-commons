"""Linux enforcement: the child runs in a transient systemd ``--user`` scope sized to its claim.

``systemd-run --user --scope`` puts the command into its own cgroup with ``MemoryMax`` (the claim's
bytes, the whole tree), ``MemorySwapMax=0`` (no escape into swap), ``CPUQuota`` (``cpu * 100%``) and
``RuntimeMaxSec`` (a backstop for the launcher's own wall timer). It needs a DELEGATED user cgroup,
probed once per process; without one the run is REFUSED -- an unenforced run would make the ledger
a list of hopes. A Slurm compute node (``SLURM_JOB_ID``) is the one exception: its allocation is
already a cgroup the scheduler enforces, and the Outcome's ``enforcer`` says ``slurm``.

THE WALL KILL IS THE LAUNCHER'S: it owns the deadline and kills the scope's process group, so a
``wall`` reason is a fact it observed. A ``SIGKILL`` it did not send is the cgroup OOM killer, so
reason ``memory``. Peak memory is the largest child RSS (``getrusage(RUSAGE_CHILDREN)``): the scope's
``memory.peak`` vanishes with the scope.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Final

from lab_commons.box.demand import Demand, Outcome

if sys.platform != 'win32':
    import resource

__all__ = ['contain', 'refusal', 'scope_argv']

_PROBE_S: Final = 10.0
_KIB: Final = 1024
#: POSIX SIGKILL; spelled as a number because :mod:`signal` lacks the name on Windows, where this imports.
_SIGKILL: Final = 9


def scope_argv(systemd_run: str, argv: Sequence[str], demand: Demand) -> list[str]:
    """*argv* wrapped by the *systemd_run* executable in a user scope whose limits are exactly *demand*."""
    return [
        systemd_run,
        '--user',
        '--scope',
        '--quiet',
        '-p',
        f'MemoryMax={demand.mem_bytes}',
        '-p',
        'MemorySwapMax=0',
        '-p',
        f'CPUQuota={demand.cpu * 100}%',
        '-p',
        f'RuntimeMaxSec={demand.wall_s}',
        '--',
        *argv,
    ]


@cache
def _systemd_run() -> str | None:
    """The absolute ``systemd-run`` when a user scope can actually be created here, probed once."""
    found = shutil.which('systemd-run')
    if found is None:
        return None
    try:
        probe = subprocess.run(
            [found, '--user', '--scope', '--quiet', '--', 'true'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=_PROBE_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return found if probe.returncode == 0 else None


def refusal() -> str | None:
    """Why this machine cannot enforce a claim, or ``None`` when it can."""
    if os.environ.get('SLURM_JOB_ID') or _systemd_run():
        return None
    return (
        'box: no delegated cgroup (`systemd-run --user --scope` fails) and not a Slurm job; refusing to run unenforced'
    )


def contain(
    argv: Sequence[str], demand: Demand, cwd: str | None, env: Mapping[str, str] | None, tag: tuple[str, str | None]
) -> Outcome:
    """Run *argv* enforced to *demand* (the caller checked :func:`refusal`); kill the group at the wall."""
    systemd_run = _systemd_run()
    slurm = systemd_run is None
    command = list(argv) if systemd_run is None else scope_argv(systemd_run, argv, demand)
    start = time.monotonic()
    child = subprocess.Popen(command, cwd=cwd, env=None if env is None else dict(env), start_new_session=True)
    try:
        child.wait(timeout=demand.wall_s)
        walled = False
    except subprocess.TimeoutExpired:
        _kill_group(child.pid)
        child.wait()
        walled = True
    wall_s = time.monotonic() - start
    usage = _children_usage()
    killed = child.returncode == -_SIGKILL
    why = 'wall' if walled else 'memory' if killed else 'exit'
    return Outcome(child.returncode, why, usage[0], usage[1], wall_s, 'slurm' if slurm else 'systemd-scope', *tag)


def _kill_group(pid: int) -> None:
    if sys.platform != 'win32':
        os.killpg(pid, _SIGKILL)


def _children_usage() -> tuple[int, float]:
    if sys.platform == 'win32':
        return 0, 0.0
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    return int(used.ru_maxrss) * _KIB, used.ru_utime + used.ru_stime
