"""THE PROBES -- what a host or a Slurm account has NOW, read from the system and returned as a :class:`Supply`.

Three sources, one value (:mod:`lab_commons.compute.model`):

* **box**: ``cpu`` = the cores free now, from the OS's busy time over a short window (load average where the
  OS keeps no per-core times); ``mem_gb`` = the memory available now (:mod:`lab_commons.proc`); the OS
  itself (``windows``, ``linux``, ``macos``) with count = ``cpu``.
  Measured only: the host user's own reserve is a reservation, not a reading.
* **tools**: the machine file's ``[capabilities.<tool>]`` declares ``seats = N`` -- the one number a person
  declares, because no probe can read a licence. A tool is present when its probe (:data:`TOOL_PROBES`) finds
  it installed, or, without a probe, when it is declared. Its count is ``seats`` less the running jobs needing it.
* **slurm account**: ``cpu`` AND ``mem_gb`` over EVERY grant of the account (:func:`account`), plus ``submits``
  -- the job submissions its QOS still accepts (infinite when uncapped).

The readers of the OS are module functions taken as defaults, so a test passes fakes; the arithmetic is pure.
"""

from __future__ import annotations

import ctypes
import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from lab_commons.compute.model import Resources, Supply
from lab_commons.hpc.plan import headroom
from lab_commons.hpc.slurm import Snapshot
from lab_commons.proc import SystemMemory, system_memory

if sys.platform == 'win32':
    import winreg

__all__ = [
    'OS_NAMES',
    'TOOL_PROBES',
    'account',
    'available_mem_gb',
    'box',
    'busy_fraction',
    'free_cores',
    'os_name',
    'parse_proc_stat',
    'tools',
]

#: ``sys.platform`` -> the resource name of the OS.
OS_NAMES: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}

#: How long the CPU is watched to call a core free.
WINDOW_S: Final = 0.5

_GB: Final = 1024**3


def os_name(platform: str = sys.platform) -> str:
    """The OS resource of *platform*; an OS the family does not serve is refused by name."""
    if platform not in OS_NAMES:
        msg = f'no OS resource for sys.platform {platform!r}; known: {sorted(OS_NAMES)}'
        raise ValueError(msg)
    return OS_NAMES[platform]


# --- cpu ----------------------------------------------------------------------------------------------


def parse_proc_stat(text: str) -> tuple[float, float]:
    """``/proc/stat``'s aggregate ``cpu`` line as ``(idle, total)`` jiffies; idle counts iowait."""
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == 'cpu':
            ticks = [float(one) for one in fields[1:9]]
            return ticks[3] + ticks[4], sum(ticks)
    msg = 'no aggregate cpu line in /proc/stat'
    raise ValueError(msg)


def busy_fraction(before: tuple[float, float], after: tuple[float, float]) -> float:
    """The busy share of the CPU between two ``(idle, total)`` samples, in [0, 1]."""
    total = after[1] - before[1]
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, 1.0 - (after[0] - before[0]) / total))


def _times_linux() -> tuple[float, float]:
    return parse_proc_stat(Path('/proc/stat').read_text(encoding='ascii'))


def _times_windows() -> tuple[float, float]:
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.WinDLL('kernel32').GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        msg = 'GetSystemTimes failed'
        raise OSError(msg)
    return float(idle.value), float(kernel.value + user.value)  # kernel time includes idle time


def _busy_cores(cores: int, window_s: float, sleep: Callable[[float], None]) -> float:
    if sys.platform in {'linux', 'win32'}:
        sample = _times_linux if sys.platform == 'linux' else _times_windows
        before = sample()
        sleep(window_s)
        return cores * busy_fraction(before, sample())
    return os.getloadavg()[0]  # no per-core times in the stdlib here: the 1-minute run queue


def free_cores(
    *,
    cores: int | None = None,
    busy: Callable[[int, float, Callable[[float], None]], float] = _busy_cores,
    window_s: float = WINDOW_S,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """The cores free now: the core count less the cores busy over *window_s*, floored at zero."""
    count = cores if cores is not None else os.cpu_count() or 1
    return max(0.0, count - busy(count, window_s, sleep))


# --- memory -------------------------------------------------------------------------------------------


def available_mem_gb(read: Callable[[], SystemMemory | None] = system_memory) -> float:
    """The memory available to a new process now, in GB (:func:`lab_commons.proc.system_memory`); unread is 0."""
    memory = read()
    return 0.0 if memory is None else memory.available_bytes / _GB


# --- tools --------------------------------------------------------------------------------------------


def _com_registered(*progids: str) -> Callable[[], bool]:
    def probe() -> bool:
        if sys.platform != 'win32':
            return False
        try:
            for progid in progids:
                winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'{progid}\\CLSID').Close()
        except OSError:
            return False
        return True

    return probe


def _on_path(program: str) -> Callable[[], bool]:
    return lambda: shutil.which(program) is not None


#: Tool -> whether it is installed here. A tool without a probe is present when declared.
TOOL_PROBES: Final[Mapping[str, Callable[[], bool]]] = {
    'jmag': _com_registered('designer.Application', 'scheduler.JobApplication'),
    'matlab': _on_path('matlab'),
    'node': _on_path('node'),
}


def tools(
    capabilities: Mapping[str, Mapping[str, Any]],
    *,
    running: Iterable[frozenset[str]] = (),
    probes: Mapping[str, Callable[[], bool]] = TOOL_PROBES,
) -> tuple[Resources, frozenset[str]]:
    """``(counts, present)`` of the declared tools: ``seats`` (default 1) less the *running* need-sets using each.

    A declared tool its probe does not find is ABSENT, whatever its seats say.
    """
    held = list(running)
    present = frozenset(name for name in capabilities if name not in probes or probes[name]())
    counts = {
        name: max(0, int(capabilities[name].get('seats', 1)) - sum(name in needs for needs in held)) for name in present
    }
    return Resources(counts), present


# --- the two kinds of supply ------------------------------------------------------------------------------


def box(
    scope: str,
    *,
    capabilities: Mapping[str, Mapping[str, Any]] | None = None,
    running: Sequence[frozenset[str]] = (),
    cores: Callable[[], float] = free_cores,
    mem_gb: Callable[[], float] = available_mem_gb,
    platform: str = sys.platform,
    probes: Mapping[str, Callable[[], bool]] = TOOL_PROBES,
) -> Supply:
    """The box now: free cores, available memory, its OS (count = cores) and its declared tools."""
    cpu = cores()
    system = os_name(platform)
    counts, present = tools(capabilities or {}, running=running, probes=probes)
    measured = Resources({'cpu': cpu, 'mem_gb': mem_gb(), system: cpu}) + counts
    return Supply(scope, measured, present | measured.names | {system})


def account(
    scope: str,
    snapshots: Sequence[tuple[Snapshot, str]],
) -> Supply:
    """A Slurm account over EVERY grant: ``(snapshot, qos)`` each, summed; ``submits`` infinite if uncapped.

    The account's OS, ``linux``, counts as many as ``cpu``.
    ``cpu`` per grant is :func:`lab_commons.hpc.plan.headroom` (quota less what is held); ``mem_gb`` the
    quota's remaining memory, else -- uncapped -- the free memory of the schedulable nodes.
    """
    cpu = mem = submits = 0.0
    for snapshot, qos in snapshots:
        cpu += headroom(snapshot)
        left = snapshot.quota.remaining()['mem']
        mem += (left if left is not None else sum(n.mem_mb for n in snapshot.nodes if n.schedulable)) / 1024
        room = snapshot.quota.submit_room(qos)
        submits += math.inf if room is None else room
    linux = OS_NAMES['linux']  # a Slurm account runs on linux: the OS is a resource there like any other
    measured = Resources({'cpu': cpu, 'mem_gb': mem, 'submits': submits, linux: cpu})
    return Supply(scope, measured, frozenset({'cpu', 'mem_gb', 'submits', linux}))
