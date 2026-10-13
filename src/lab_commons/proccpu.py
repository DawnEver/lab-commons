"""Tier 1 -- the cpu-seconds a process, and a process TREE, has used: read from the kernel, never inferred.

Beside :mod:`lab_commons.proc` (memory and the process table, whose tree walk this reads) so each stays
one subject. Stdlib only, as tier 1 requires: ``GetProcessTimes`` on Windows, ``/proc/<pid>/stat``
elsewhere; no darwin backend (``None`` there, never ``0``).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from pathlib import Path

from lab_commons.proc import process_tree

__all__ = ['TreeCpu', 'cpu_seconds', 'process_times']

#: ``OpenProcess`` access enough to read a process's times.
_QUERY_LIMITED = 0x1000


#: ``GetProcessTimes`` counts in 100-nanosecond intervals.
_FILETIME_PER_S = 10_000_000


def process_times(pid: int) -> tuple[int, float] | None:
    """``(creation stamp, user + system cpu-seconds)`` of *pid*, or ``None`` when it cannot be read.

    The stamp identifies the PROCESS, not the number: a recycled pid reads a different one. It is
    a FILETIME on Windows and the start time in clock ticks from ``/proc/<pid>/stat`` elsewhere;
    only equality is ever asked of it. No darwin backend: ``None`` there.
    """
    if pid <= 0:
        return None
    if os.name != 'nt':
        return None if sys.platform == 'darwin' else _process_times_procfs(pid)
    kernel32 = ctypes.WinDLL('kernel32')
    handle = kernel32.OpenProcess(_QUERY_LIMITED, False, pid)  # noqa: FBT003 -- bInheritHandle, positional per the Win32 signature
    if not handle:
        return None
    try:
        created, exited, kernel_t, user_t = (ctypes.c_ulonglong() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        return (int(created.value), (kernel_t.value + user_t.value) / _FILETIME_PER_S) if ok else None
    finally:
        kernel32.CloseHandle(handle)


#: In ``/proc/<pid>/stat`` after the ``)`` closing the command: utime, stime, starttime (0-based).
_STAT_UTIME, _STAT_STIME, _STAT_START = 11, 12, 19


def _process_times_procfs(pid: int) -> tuple[int, float] | None:
    """The procfs half of :func:`process_times`: the process's OWN utime + stime, never its children's.

    ``cutime``/``cstime`` are left out on purpose: a child is counted by :class:`TreeCpu` as a process of
    the tree, and adding its reaped time to the parent's would count it twice.
    """
    try:
        text = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
        fields = text[text.rindex(')') + 2 :].split()
        ticks = os.sysconf('SC_CLK_TCK')
        return int(fields[_STAT_START]), (int(fields[_STAT_UTIME]) + int(fields[_STAT_STIME])) / ticks
    except (OSError, ValueError, IndexError):
        return None


def cpu_seconds(pid: int) -> float | None:
    """User + system cpu-seconds *pid* itself has used, or ``None`` when it cannot be read (never ``0``)."""
    times = process_times(pid)
    return None if times is None else times[1]


class TreeCpu:
    """The cpu-seconds a process TREE has used, MEASURED: sampled, never inferred from wall time and cores.

    Each :meth:`sample` reads every process of the tree now and keeps, per process (pid AND creation
    stamp, so a recycled pid is a new process), the most it was seen using. A child that exits keeps its
    last reading; one that starts and ends between two samples is not seen, so the sum is a FLOOR whose
    error is at most what such children used.
    """

    def __init__(
        self,
        pid: int,
        *,
        tree: Callable[[int], frozenset[int]] | None = None,
        times: Callable[[int], tuple[int, float] | None] | None = None,
    ) -> None:
        """Meter *pid*'s tree; *tree* and *times* are the process-table seams (default: this box's)."""
        self._pid = pid
        self._tree = tree or process_tree
        self._times = times or process_times
        self._seen: dict[tuple[int, int], float] = {}

    def sample(self) -> float | None:
        """The tree's cpu-seconds so far, or ``None`` while no process of it has ever been read."""
        for member in self._tree(self._pid):
            read = self._times(member)
            if read is not None:
                key = (member, read[0])
                self._seen[key] = max(self._seen.get(key, 0.0), read[1])
        return sum(self._seen.values()) if self._seen else None
