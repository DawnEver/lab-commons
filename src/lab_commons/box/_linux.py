"""Linux enforcement: the child runs in a transient systemd scope sized to its claim.

``systemd-run --scope`` puts the command into its own cgroup with ``MemoryMax`` (the claim's
bytes, the whole tree), ``MemorySwapMax=0`` (no escape into swap) and ``CPUQuota`` (``cpu * 100%``); the wall is the
launcher's (systemd 239 refuses ``RuntimeMaxSec`` on a scope). The MANAGER follows the caller: root
(a system service, e.g. the supervisor) scopes in the system manager, anyone else in its own
``--user`` manager, which needs the ``memory`` and ``cpu`` controllers DELEGATED. Probed once per
process; a manager that cannot bind the limits REFUSES the run -- an unenforced run would make the
ledger a list of hopes.

A SLURM COMPUTE NODE (this process in the cgroup of job ``SLURM_JOB_ID``, no scope) is enforced by the
LAUNCHER, per run: the allocation's cgroup bounds the whole job, not this run, so the launcher watches the run's process
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
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons.box.demand import ProcessEnd, cores, mem_bytes
from lab_commons.compute.model import Demand
from lab_commons.proc import working_set_bytes

if sys.platform != 'win32':
    import resource

__all__ = ['Manager', 'contain', 'in_slurm_job', 'manager', 'refusal', 'scope_argv']

_PROBE_S: Final = 10.0
_KIB: Final = 1024
#: POSIX SIGKILL; spelled as a number because :mod:`signal` lacks the name on Windows, where this imports.
_SIGKILL: Final = 9
#: Seconds between the launcher's looks at the run (wall clock; group memory where it watches).
_TICK_S: Final = 0.05
#: Index of ``pgrp`` in ``/proc/<pid>/stat`` after the ``(comm)`` field.
_PGRP_FIELD: Final = 2


@dataclass(frozen=True)
class Manager:
    """The systemd manager a scope is started in: its ``systemd-run`` flags and its cgroup's controller list."""

    flags: tuple[str, ...]
    controllers: Path


def manager(uid: int) -> Manager:
    """Root's scopes go to the system manager; any other *uid*'s to its own user manager."""
    if uid == 0:
        return Manager((), Path('/sys/fs/cgroup/cgroup.controllers'))
    slice_ = f'/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service'
    return Manager(('--user',), Path(f'{slice_}/cgroup.controllers'))


def scope_argv(systemd_run: str, where: Manager, argv: Sequence[str], demand: Demand) -> list[str]:
    """*argv* wrapped by the *systemd_run* executable in a scope of *where* whose limits are exactly *demand*."""
    return [
        systemd_run,
        *where.flags,
        '--scope',
        '--quiet',
        '-p',
        f'MemoryMax={mem_bytes(demand)}',
        '-p',
        'MemorySwapMax=0',
        '-p',
        f'CPUQuota={cores(demand) * 100}%',
        '--',
        *argv,
    ]


@cache
def _systemd_run() -> str | None:
    """The absolute ``systemd-run`` when a scope of :func:`_here` can actually ENFORCE, probed once.

    A scope that merely STARTS is not enough, MEASURED 2026-10-14 on an Ada login node (systemd 239,
    cgroup v1): ``-p MemoryMax=200M`` was accepted and a 400 MB child ran to completion. Limits bind
    only on the unified hierarchy (cgroup v2) with ``memory`` and ``cpu`` in the manager's cgroup.
    """
    found = shutil.which('systemd-run')
    if found is None or not _delegated():
        return None
    try:
        probe = _door.run(
            [found, *_here().flags, '--scope', '--quiet', '--', 'true'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=_PROBE_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return found if probe.returncode == 0 else None


def _here() -> Manager:
    """Return the manager of the running process, by its effective uid."""
    return manager(os.geteuid() if sys.platform != 'win32' else 0)


def _delegated() -> bool:
    """Whether this is cgroup v2 and this manager's cgroup holds the ``memory`` and ``cpu`` controllers."""
    try:
        controllers = set(_here().controllers.read_text(encoding='utf-8').split())
    except OSError:
        return False
    return {'memory', 'cpu'} <= controllers


def in_slurm_job(job: str | None, cgroups: str) -> bool:
    """Whether *cgroups* (``/proc/self/cgroup``) places this process inside Slurm job *job*.

    The variable alone is not evidence: it is inherited by anything a job's shell starts and can be
    exported by hand, and a run taken onto the watched path on its word alone has no cgroup at all.
    """
    return bool(job) and f'/job_{job}/' in cgroups


def _slurm_job() -> bool:
    try:
        cgroups = Path('/proc/self/cgroup').read_text(encoding='utf-8')
    except OSError:
        return False
    return in_slurm_job(os.environ.get('SLURM_JOB_ID'), cgroups)


def refusal() -> str | None:
    """Why this machine cannot enforce a claim, or ``None`` when it can."""
    if _slurm_job() or _systemd_run():
        return None
    return (
        'box: no cgroup v2 memory+cpu for a `systemd-run --scope` here, and not a Slurm job; refusing to run unenforced'
    )


def contain(
    argv: Sequence[str],
    demand: Demand,
    wall_s: int,
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    tag: tuple[str, str | None],
) -> ProcessEnd:
    """Run *argv* enforced to *demand* and *wall_s* (the caller checked :func:`refusal`); its group dies with it."""
    systemd_run = _systemd_run()
    command = list(argv) if systemd_run is None else scope_argv(systemd_run, _here(), argv, demand)
    watch = systemd_run is None
    start = time.monotonic()
    child = _door.popen(command, cwd=cwd, env=None if env is None else dict(env), start_new_session=True)
    deadline, why, peak, limit = start + wall_s, 'exit', 0, mem_bytes(demand)
    while child.poll() is None:
        held = _group_resident(child.pid) if watch else 0
        peak = max(peak, held)
        if held > limit or time.monotonic() >= deadline:
            why = 'memory' if held > limit else 'wall'
            _kill_group(child.pid)
            child.wait()
            break
        time.sleep(_TICK_S)
    _kill_group(child.pid)  # what the child left behind in its group ends with the claim
    if why == 'exit' and child.returncode == -_SIGKILL:
        why = 'memory'  # a SIGKILL the launcher did not send is the scope's OOM killer
    usage = _children_usage()
    enforcer = 'slurm' if watch else 'systemd-scope'
    return ProcessEnd(child.returncode, why, max(peak, usage[0]), usage[1], time.monotonic() - start, enforcer, *tag)


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
