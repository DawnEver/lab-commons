"""Tier 1 -- reading this box: its memory, its process table, and ending a process TREE.

WHY THIS IS IN LAB-COMMONS AND NOT IN A CONSUMER. motronics-studio carried TWO copies of the
``_MEMORYSTATUSEX`` ctypes structure -- one in ``core/utils/proc.py`` (production) and one in
``scripts/gate/width.py`` (its dev tree) -- not by accident but because its layering rule forbids
the dev tree importing production code. Two copies of one syscall wrapper is two things to keep
true, and the failure mode is silent: the copies drift, one starts sizing off TOTAL where the
other sizes off AVAILABLE, and nothing reds. An INSTALLED package is importable from both sides of
that firewall, which is the only arrangement that ends the duplication instead of minting a third
copy. (motronics-studio ``docs-src/dev/compute-resources.md``, "the constraint any unification
must reckon with first".)

TIER 1, and the test is the package's own: this module imports ``ctypes``, ``os``, ``signal``,
``subprocess`` and ``pathlib`` -- stdlib only. It names no solver, winding, optimizer or vendor
tool, and it never pulls ``lab_commons.em`` in. A lab doing chemistry could use it unchanged.

NOT ``psutil``, and the reason is a property rather than a preference: ``psutil`` is an optional
extra in every consumer here, so a ``psutil``-only reader answers on the boxes that happen to have
it and answers NOTHING on the ones that do not -- and a caller that reads "no reading" as "no
constraint" has silently dropped the ceiling it was written to honour, on exactly the machines
that could not be checked. ``GlobalMemoryStatusEx`` is in ``kernel32`` and ``/proc`` is in the
kernel, so on the platforms this family runs there is ALWAYS a reading. Adding ``psutil`` as a
hard dependency of tier 1 would also make "install nothing heavier than this" false for a consumer
that wanted only logging.

Provenance: extracted from motronics-studio's ``core/utils/proc.py`` (2026-09-13). The
Windows-specific incident notes kept in the docstrings below are MEASUREMENTS on that fleet and
are what justify each choice against the more obvious one; they are carried across deliberately,
because a rule whose evidence is deleted is the next thing somebody "simplifies".
"""

from __future__ import annotations

import ctypes
import os
import signal
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    'SystemMemory',
    'descendants',
    'kill_pid',
    'kill_process_tree',
    'pid_alive',
    'process_tree',
    'system_memory',
    'working_set_bytes',
]

#: The signal that means what ``taskkill /F`` means: un-catchable, no cleanup.
#:
#: RESOLVED rather than spelled inline, because ``signal.SIGKILL`` DOES NOT EXIST on Windows --
#: referencing it directly makes the POSIX branch unimportable from the only machines this fleet
#: currently has, so the code written for macOS and Linux would ship never having been executed
#: anywhere. The fallback is never taken on a real POSIX box; it exists so the constant is
#: DEFINABLE everywhere, which is what lets that branch be tested from here.
_KILL_SIGNAL = getattr(signal, 'SIGKILL', signal.SIGTERM)

_STILL_ACTIVE = 259
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002


def _kernel32():
    """``kernel32``, or ``None`` off Windows.

    Reached by ``getattr`` rather than as ``ctypes.windll.kernel32``: ``windll`` does not exist
    off Windows, so the attribute expression is unresolvable to a type checker on every platform
    at once and would need a suppression to say so. This puts that fact in the CODE, where the
    ``None`` returns already handle it, instead of in a comment the checker is told to ignore.
    """
    windll = getattr(ctypes, 'windll', None)
    return None if windll is None else windll.kernel32


# --------------------------------------------------------------------------------------------
# Memory: the one home for this reading
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SystemMemory:
    """Total and available RAM of the machine, in bytes, as the OS reports it.

    BYTES, not gigabytes, and no rounding: a caller that wants a headline figure can round one,
    but a caller SIZING A WORKER POOL divides by a per-worker footprint, and a value already
    rounded to 0.1 GB has thrown away the only precision that division has.
    """

    total_bytes: int
    available_bytes: int

    @property
    def used_bytes(self) -> int:
        """What is not available. DERIVED, never read separately -- a ``used`` the OS reports
        alongside ``available`` need not sum to ``total``, and two readings of one quantity that
        disagree is a third number neither backend produced.
        """
        return self.total_bytes - self.available_bytes

    @property
    def used_fraction(self) -> float:
        """Occupied share of total, in ``[0, 1]``. Unitless on purpose -- a percent is a
        presentation choice and belongs where the number is rendered, not where it is read.
        """
        return self.used_bytes / self.total_bytes


class _MEMORYSTATUSEX(ctypes.Structure):
    """The ``kernel32`` struct, spelled ONCE for this whole family of repos.

    ``dwLength`` MUST be set to ``sizeof`` before the call -- the API validates it and fails the
    whole read otherwise, which is why this is a declared structure rather than a tuple of offsets.
    """

    _fields_ = (
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    )


def system_memory() -> SystemMemory | None:
    """Total and available RAM, or ``None`` when this machine's memory cannot be read.

    ``None`` is reserved for a machine that genuinely cannot be read -- a platform with neither
    backend, or a syscall that failed. UNREADABLE IS NOT THE SAME FACT AS ZERO, and only the first
    may be treated as "unknown"; a caller enforcing a floor must not read ``None`` as "plenty".

    Returns:
        A :class:`SystemMemory`, or ``None`` when unreadable.

    """
    return _system_memory_windows() if os.name == 'nt' else _system_memory_procfs()


def _system_memory_windows() -> SystemMemory | None:
    """PHYSICAL RAM via ``GlobalMemoryStatusEx``, never the page file.

    ``ullTotalPageFile`` is the commit limit and on a box with a large swap file it exceeds
    physical RAM severalfold. Sizing a pool of solver processes against it would promise memory
    that has to be paged to exist, and an FEA worker that pages does not run slowly -- it thrashes.
    """
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    try:
        ok = kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None
    if not ok or status.ullTotalPhys <= 0:
        return None
    return SystemMemory(total_bytes=int(status.ullTotalPhys), available_bytes=int(status.ullAvailPhys))


def _system_memory_procfs() -> SystemMemory | None:
    """``/proc/meminfo`` on Linux. ``MemAvailable``, NOT ``MemFree``.

    ``MemFree`` excludes the page cache, which the kernel hands back on demand, so a healthy Linux
    box with a warm cache reports single-digit ``MemFree`` and would size every pool to one worker.
    ``MemAvailable`` is the kernel's own estimate of what a new allocation can actually get, and a
    kernel too old to publish it is reported UNREADABLE rather than approximated -- the fallback
    would be exactly the silent-degradation shape this module exists to avoid.
    """
    try:
        text = Path('/proc/meminfo').read_text(encoding='utf-8')
    except OSError:
        return None
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(':')
        parts = rest.split()
        if len(parts) == 2 and parts[1] == 'kB':
            fields[key.strip()] = int(parts[0]) * 1024
    total, available = fields.get('MemTotal'), fields.get('MemAvailable')
    if total is None or available is None or total <= 0:
        return None
    return SystemMemory(total_bytes=total, available_bytes=available)


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    """The ``psapi`` struct. ``cb`` MUST be ``sizeof`` before the call, as with ``_MEMORYSTATUSEX``."""

    _fields_ = (
        ('cb', ctypes.c_ulong),
        ('PageFaultCount', ctypes.c_ulong),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
    )


def working_set_bytes(pid: int) -> int | None:
    """Resident memory of *pid* right now, or ``None`` when it cannot be read.

    WORKING SET, NOT PRIVATE BYTES, and the choice is the point: what exhausts a box and crashes
    it is RESIDENT pages, which is exactly what ``available_bytes`` is missing. A private-bytes
    figure counts committed-but-paged memory the box is not currently short of.

    ``None`` (gone, denied, or a platform with no backend) is distinct from ``0``. A caller
    enforcing a ceiling must NOT read ``None`` as "under the ceiling".
    """
    if pid <= 0:
        return None
    if os.name != 'nt':
        return _working_set_bytes_procfs(pid)
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        # `K32GetProcessMemoryInfo` is the kernel32 forwarder, present since Windows 7 -- using it
        # avoids loading `psapi.dll` separately and avoids the version skew between the two.
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _working_set_bytes_procfs(pid: int) -> int | None:
    """``VmRSS`` from ``/proc/<pid>/status`` -- the POSIX half, so the ceiling is enforceable there too."""
    try:
        text = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        key, _, rest = line.partition(':')
        if key.strip() == 'VmRSS':
            parts = rest.split()
            if len(parts) == 2 and parts[1] == 'kB':
                return int(parts[0]) * 1024
    return None


# --------------------------------------------------------------------------------------------
# Liveness and the process table
# --------------------------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether *pid* is a RUNNING process. THE ONLY STALENESS TEST -- never a clock.

    A clock cannot tell a long solve from a dead one, and a transient FEA run legitimately takes
    hours; any timeout short enough to reap a corpse is short enough to evict a live job.

    On POSIX a signal-0 probe is exact. On Windows ``os.kill(pid, 0)`` is NOT -- it answers
    affirmatively for a pid that has already exited -- so ``OpenProcess`` + ``GetExitCodeProcess``
    is used, and only ``STILL_ACTIVE`` (259) counts.
    """
    if pid <= 0:
        return False
    if os.name == 'nt':
        kernel32 = _kernel32()
        if kernel32 is None:
            return False
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _processentry32w() -> type[ctypes.Structure]:
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = (
            ('dwSize', ctypes.c_ulong),
            ('cntUsage', ctypes.c_ulong),
            ('th32ProcessID', ctypes.c_ulong),
            ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
            ('th32ModuleID', ctypes.c_ulong),
            ('cntThreads', ctypes.c_ulong),
            ('th32ParentProcessID', ctypes.c_ulong),
            ('pcPriClassBase', ctypes.c_long),
            ('dwFlags', ctypes.c_ulong),
            ('szExeFile', ctypes.c_wchar * 260),
        )

    return PROCESSENTRY32W


def _process_rows() -> dict[int, tuple[str, int]] | None:
    """``{pid: (image, parent_pid)}`` from ONE snapshot, or ``None`` when unreadable.

    Toolhelp32 on Windows, ``/proc`` on POSIX. NOT ``tasklist``: measured broken on this fleet
    (2026-08-19, 2026-08-21) by a damaged WMI, where it reports "not found" for live pids. The
    Win32 snapshot answers the question itself rather than asking a tool that can be broken.
    """
    if os.name != 'nt':
        return _process_rows_procfs()
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    entry_type = _processentry32w()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, -1, ctypes.c_void_p(-1).value):
        return None
    rows: dict[int, tuple[str, int]] = {}
    try:
        entry = entry_type()
        entry.dwSize = ctypes.sizeof(entry_type)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            rows[int(entry.th32ProcessID)] = (str(entry.szExeFile), int(entry.th32ParentProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows or None


def _process_rows_procfs() -> dict[int, tuple[str, int]] | None:
    root = Path('/proc')
    if not root.is_dir():
        return None
    rows: dict[int, tuple[str, int]] = {}
    for child in root.iterdir():
        if not child.name.isdigit():
            continue
        try:
            fields = dict(
                line.split(':', 1)
                for line in (child / 'status').read_text(encoding='utf-8').splitlines()
                if ':' in line
            )
        except OSError:
            continue
        name, ppid = fields.get('Name', '').strip(), fields.get('PPid', '').strip()
        if ppid.isdigit():
            rows[int(child.name)] = (name, int(ppid))
    return rows or None


def _creation_time(pid: int) -> int | None:
    """The process's creation FILETIME, or ``None`` when it cannot be read.

    The Toolhelp32 snapshot carries NO creation time -- ``PROCESSENTRY32W`` has no such field --
    so this is a second, per-pid syscall. It is only ever asked about pids the walk has already
    reached, which is at most the size of one process tree.

    ``None`` on any failure. The caller treats ``None`` as "cannot disprove the edge", which keeps
    this a REFUTATION step: it removes edges it can prove false and never invents one.
    """
    if os.name != 'nt':
        return None
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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
        return int(created.value) if ok else None
    finally:
        kernel32.CloseHandle(handle)


def descendants(
    pid: int,
    rows: Mapping[int, tuple[str, int]],
    created: Callable[[int], int | None],
) -> frozenset[int]:
    """The closure of *pid* over *rows*, REFUSING an edge the creation times disprove.

    Split out of :func:`process_tree` so the pid-reuse property is testable without making Windows
    actually recycle a pid -- both *rows* and *created* are injectable.

    THE EDGE THIS REFUSES, and why it is not hypothetical. ``th32ParentProcessID`` is never
    cleared when the parent dies. That is load-bearing here -- it is what lets an orphan still be
    found -- but it also means a DEAD pid stays written on its orphans, so the moment the OS
    recycles that number the new process inherits "children" it never spawned, and
    :func:`kill_process_tree` would end them. MEASURED 2026-09-06 on the origin box: 32 of 259 live
    processes carried a parent pid that no longer existed, across 28 distinct dead pids -- 28
    outstanding landmines at one instant, on an ordinary desktop.

    A child cannot predate its parent, so an edge whose claimed child was created STRICTLY EARLIER
    is impossible and is dropped. EQUAL stamps are KEPT: FILETIME granularity is coarse enough that
    a fast spawn shares its parent's value, and dropping those would lose real children -- the
    direction that leaves survivors, which is the defect this exists to prevent.
    """
    children: dict[int, list[int]] = {}
    for child_pid, (_, parent_pid) in rows.items():
        children.setdefault(parent_pid, []).append(child_pid)
    seen: set[int] = set()
    stack = [pid] if pid in rows else []
    while stack:
        current = stack.pop()
        if current in seen:
            # A pid cannot be its own ancestor, but the table is a snapshot of a mutating machine
            # and pid reuse can forge a cycle. Guarding here keeps the walk terminating.
            continue
        seen.add(current)
        current_created = created(current)
        for child in children.get(current, ()):
            child_created = created(child)
            if current_created is not None and child_created is not None and child_created < current_created:
                continue  # impossible edge: the "child" predates the parent, so the pid was reused
            stack.append(child)
    return frozenset(seen)


def process_tree(pid: int) -> frozenset[int]:
    """*pid* and every process descended from it, from ONE snapshot of the process table.

    ONE SNAPSHOT, NOT A LIVE WALK, and that is the whole correctness argument. A parent's death
    does not re-parent its children on Windows and does not clear their recorded parent: MEASURED
    2026-08-30 on the origin box, a ``triangle.exe`` whose ``cmd.exe`` parent had been killed still
    reported that dead parent's pid. So a walk that re-reads the table after each kill loses the
    rest of the tree the moment the root dies, and a walk starting from the LEAVES cannot find them
    at all. Collecting the closure first and killing afterwards is what makes
    :func:`kill_process_tree` total.

    Returns:
        The closure INCLUDING *pid*. ``frozenset()`` -- never ``{pid}`` -- when the table cannot be
        read, so a caller cannot mistake an unreadable machine for a childless process and report
        having reaped something it never saw.

    """
    rows = _process_rows()
    return frozenset() if rows is None else descendants(pid, rows, _creation_time)


def kill_pid(pid: int) -> bool:
    """Force-kill one pid. Returns whether it was actually asked to die.

    IT RETURNS A VERDICT because the version this was lifted from could not fail: it ran
    ``taskkill`` and suppressed ``OSError``, so on any non-Windows machine every call was a silent
    no-op reporting success. A cleanup that always reports success is, from the outside, the same
    shape as a cleanup that works.

    ``ProcessLookupError`` is folded into ``False`` ON PURPOSE: a process that was already gone was
    not killed BY US, and saying otherwise lets a caller conclude it reaped something it never
    touched.
    """
    if pid <= 0:
        return False
    if os.name == 'nt':
        kernel32 = _kernel32()
        if kernel32 is None:
            return False
        handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            ok = kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)
        return bool(ok)
    try:
        os.kill(pid, _KILL_SIGNAL)
    except OSError:
        return False
    return True


def kill_process_tree(pid: int) -> frozenset[int]:
    """Force-kill *pid* and every descendant. Returns the pids actually confirmed killed.

    DESCENDANTS BEFORE THE ROOT. Killing the root first is what leaves survivors: children are not
    reaped by their parent's death, they are merely orphaned, and once orphaned nothing links them
    back to the tree that spawned them. MEASURED 2026-08-30 -- killing a ``cmd.exe`` left its
    ``triangle.exe`` child running at a full core with an unresolvable parent, which is precisely
    how 39 of them accumulated over two days.

    Returns:
        Only the pids :func:`kill_pid` CONFIRMED it terminated, so the count is an observation of a
        cleanup that happened rather than the size of the tree we intended to end.

    """
    tree = process_tree(pid)
    ordered = sorted(tree - {pid}) + ([pid] if pid in tree else [])
    return frozenset(member for member in ordered if kill_pid(member))
