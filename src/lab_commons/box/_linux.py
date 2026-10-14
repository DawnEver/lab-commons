"""Linux enforcement: the child runs in a transient systemd ``--user`` scope sized to its claim.

``systemd-run --user --scope`` puts the command into its own cgroup with ``MemoryMax`` (the claim's
bytes, the whole tree), ``MemorySwapMax=0`` (no escape into swap) and ``CPUQuota`` (``cpu * 100%``); the wall is the
launcher's (systemd 239 refuses ``RuntimeMaxSec`` on a scope). It needs a DELEGATED user cgroup,
probed once per process; without one the run is REFUSED -- an unenforced run would make the ledger
a list of hopes.

A SLURM COMPUTE NODE (``SLURM_JOB_ID``, no user scope) is enforced by the LAUNCHER, per run: the
allocation's cgroup bounds the whole job, not this run, so the launcher watches the run's process
group itself -- every 50 ms it sums the group's resident memory and kills the group past the claim
(reason ``memory``), and kills it at the wall. WHAT STAYS UNENFORCED there, stated: a spike faster
than one tick (bounded only by the allocation), a process that leaves the group with ``setsid``, and
the CPU claim (only the allocation's cpuset bounds it). ``enforcer`` says ``slurm``.

ON BOTH, THE WALL KILL IS THE LAUNCHER'S and the process group is killed when the child ends, so
nothing the run spawned outlives its claim. A ``SIGKILL`` the launcher did not send is the scope's
OOM killer, so reason ``memory``. Peak memory is the larger of the watched group sum and the largest
child RSS (``getrusage(RUSAGE_CHILDREN)``): the scope's ``memory.peak`` vanishes with the scope.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Final

from lab_commons.box.demand import Demand, Outcome
from lab_commons.proc import working_set_bytes

if sys.platform != 'win32':
    import resource

__all__ = ['contain', 'refusal', 'scope_argv']

_PROBE_S: Final = 10.0
_KIB: Final = 1024
#: POSIX SIGKILL; spelled as a number because :mod:`signal` lacks the name on Windows, where this imports.
_SIGKILL: Final = 9
#: Seconds between the launcher's looks at the run (wall clock; group memory where it watches).
_TICK_S: Final = 0.05
#: Index of ``pgrp`` in ``/proc/<pid>/stat`` after the ``(comm)`` field.
_PGRP_FIELD: Final = 2


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
        '--',
        *argv,
    ]


@cache
def _systemd_run() -> str | None:
    """The absolute ``systemd-run`` when a user scope here can actually ENFORCE, probed once.

    A scope that merely STARTS is not enough, MEASURED 2026-10-14 on an Ada login node (systemd 239,
    cgroup v1): ``-p MemoryMax=200M`` was accepted and a 400 MB child ran to completion. Limits bind
    only on the unified hierarchy (cgroup v2) with ``memory`` and ``cpu`` DELEGATED to the user manager.
    """
    found = shutil.which('systemd-run')
    if found is None or not _delegated():
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


def _delegated() -> bool:
    """Whether this is cgroup v2 and the user manager's cgroup was handed the ``memory`` and ``cpu`` controllers."""
    uid = os.getuid() if sys.platform != 'win32' else 0
    manager = Path(f'/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers')
    try:
        controllers = set(manager.read_text(encoding='utf-8').split())
    except OSError:
        return False
    return {'memory', 'cpu'} <= controllers


def refusal() -> str | None:
    """Why this machine cannot enforce a claim, or ``None`` when it can."""
    if os.environ.get('SLURM_JOB_ID') or _systemd_run():
        return None
    return (
        'box: no delegated cgroup v2 for `systemd-run --user --scope`, and not a Slurm job; refusing to run unenforced'
    )


def contain(
    argv: Sequence[str], demand: Demand, cwd: str | None, env: Mapping[str, str] | None, tag: tuple[str, str | None]
) -> Outcome:
    """Run *argv* enforced to *demand* (the caller checked :func:`refusal`); the group dies with the run."""
    systemd_run = _systemd_run()
    command = list(argv) if systemd_run is None else scope_argv(systemd_run, argv, demand)
    watch = systemd_run is None
    start = time.monotonic()
    child = subprocess.Popen(command, cwd=cwd, env=None if env is None else dict(env), start_new_session=True)
    deadline, why, peak = start + demand.wall_s, 'exit', 0
    while child.poll() is None:
        held = _group_resident(child.pid) if watch else 0
        peak = max(peak, held)
        if held > demand.mem_bytes or time.monotonic() >= deadline:
            why = 'memory' if held > demand.mem_bytes else 'wall'
            _kill_group(child.pid)
            child.wait()
            break
        time.sleep(_TICK_S)
    _kill_group(child.pid)  # what the child left behind in its group ends with the claim
    if why == 'exit' and child.returncode == -_SIGKILL:
        why = 'memory'  # a SIGKILL the launcher did not send is the scope's OOM killer
    usage = _children_usage()
    enforcer = 'slurm' if watch else 'systemd-scope'
    return Outcome(child.returncode, why, max(peak, usage[0]), usage[1], time.monotonic() - start, enforcer, *tag)


def _group_resident(pgid: int) -> int:
    """Summed resident bytes of every live process in group *pgid* (``/proc``; 0 where unreadable)."""
    total = 0
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = stat.read_text(encoding='utf-8').rsplit(')', 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > _PGRP_FIELD and int(fields[_PGRP_FIELD]) == pgid:
            total += working_set_bytes(int(stat.parent.name)) or 0
    return total


def _kill_group(pgid: int) -> None:
    if sys.platform != 'win32':
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, _SIGKILL)


def _children_usage() -> tuple[int, float]:
    if sys.platform == 'win32':
        return 0, 0.0
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    return int(used.ru_maxrss) * _KIB, used.ru_utime + used.ru_stime
