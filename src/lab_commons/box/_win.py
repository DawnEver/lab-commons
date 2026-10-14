"""Windows enforcement: the child runs inside a Job Object sized exactly to its claim.

THE ONE HOME OF JOB OBJECT CODE IN THIS PACKAGE (stdlib ``ctypes`` only). The job carries
``JOB_MEMORY`` (the claim's bytes, summed over the whole tree), ``KILL_ON_JOB_CLOSE`` (the launcher's
death closes its handle, and the kernel kills every process in the job -- nothing outlives the claim)
and ``DIE_ON_UNHANDLED_EXCEPTION`` (no WER dialog holding a dead process's memory). The CPU claim is a
rate-control HARD CAP of ``cpu / cores`` of the machine; a claim of every core sets none.

NO GRANDCHILD ESCAPES: the child is created ``CREATE_SUSPENDED``, assigned to the job, and only then
resumed (its threads, via a documented Toolhelp thread snapshot), so it cannot spawn before it is in.

A MEMORY KILL IS TOLD APART FROM A WALL KILL by who decides: the kernel only FAILS the allocation that
crosses ``JOB_MEMORY`` and posts ``JOB_OBJECT_MSG_JOB_MEMORY_LIMIT`` to the job's completion port; the
launcher reads that message and terminates the job (reason ``memory``). The wall timer is the
launcher's own clock (reason ``wall``). The CPU cap throttles and never kills, so there is no ``cpu``
reason.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from typing import Final

from lab_commons.box.demand import Demand, Outcome

__all__ = ['contain']

_CREATE_SUSPENDED: Final = 0x00000004
_LIMIT_JOB_MEMORY: Final = 0x00000200
_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: Final = 0x00000400
_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_BASIC_ACCOUNTING: Final = 1
_ASSOCIATE_COMPLETION_PORT: Final = 7
_EXTENDED_LIMIT: Final = 9
_CPU_RATE_CONTROL: Final = 15
_CPU_RATE_ENABLE: Final = 0x1
_CPU_RATE_HARD_CAP: Final = 0x4
_MSG_JOB_MEMORY_LIMIT: Final = 10
_THREAD_SUSPEND_RESUME: Final = 0x0002
_SNAPTHREAD: Final = 0x00000004
_KILLED: Final = 0xC0000017  # STATUS_NO_MEMORY / the exit code a terminated job reports
_TICK_S: Final = 0.05
_PROCESS_SET_QUOTA: Final = 0x0100
_PROCESS_TERMINATE: Final = 0x0001


class _Basic(ctypes.Structure):
    _fields_ = (
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    )


class _Extended(ctypes.Structure):
    _fields_ = (
        ('BasicLimitInformation', _Basic),
        ('IoInfo', ctypes.c_uint64 * 6),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    )


class _Accounting(ctypes.Structure):
    _fields_ = (
        ('TotalUserTime', ctypes.c_int64),
        ('TotalKernelTime', ctypes.c_int64),
        ('ThisPeriodTotalUserTime', ctypes.c_int64),
        ('ThisPeriodTotalKernelTime', ctypes.c_int64),
        ('TotalPageFaultCount', wintypes.DWORD),
        ('TotalProcesses', wintypes.DWORD),
        ('ActiveProcesses', wintypes.DWORD),
        ('TotalTerminatedProcesses', wintypes.DWORD),
    )


class _CpuRate(ctypes.Structure):
    _fields_ = (('ControlFlags', wintypes.DWORD), ('CpuRate', wintypes.DWORD))


class _Port(ctypes.Structure):
    _fields_ = (('CompletionKey', ctypes.c_void_p), ('CompletionPort', wintypes.HANDLE))


class _ThreadEntry(ctypes.Structure):
    _fields_ = (
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ThreadID', wintypes.DWORD),
        ('th32OwnerProcessID', wintypes.DWORD),
        ('tpBasePri', wintypes.LONG),
        ('tpDeltaPri', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
    )


def _k32() -> ctypes.WinDLL:
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateIoCompletionPort.restype = wintypes.HANDLE
    k32.CreateIoCompletionPort.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenThread.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetQueuedCompletionStatus.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
    )
    return k32


def _check(ok: object, what: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), f'box: {what} failed')


def _set(k32: ctypes.WinDLL, job: int, info_class: int, info: ctypes.Structure, what: str) -> None:
    _check(k32.SetInformationJobObject(wintypes.HANDLE(job), info_class, ctypes.byref(info), ctypes.sizeof(info)), what)


def _job(k32: ctypes.WinDLL, demand: Demand) -> tuple[int, int]:
    """A job sized to *demand*, and the completion port its limit messages arrive on."""
    job = k32.CreateJobObjectW(None, None)
    _check(job, 'CreateJobObjectW')
    limits = _Extended()
    limits.BasicLimitInformation.LimitFlags = (
        _LIMIT_JOB_MEMORY | _LIMIT_KILL_ON_JOB_CLOSE | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    limits.JobMemoryLimit = demand.mem_bytes
    _set(k32, job, _EXTENDED_LIMIT, limits, 'job memory limit')
    cores = os.cpu_count() or demand.cpu
    if demand.cpu < cores:
        rate = _CpuRate(_CPU_RATE_ENABLE | _CPU_RATE_HARD_CAP, max(1, demand.cpu * 10000 // cores))
        _set(k32, job, _CPU_RATE_CONTROL, rate, 'job cpu hard cap')
    port = k32.CreateIoCompletionPort(wintypes.HANDLE(-1), None, None, 1)
    _check(port, 'CreateIoCompletionPort')
    link = _Port(None, port)
    _set(k32, job, _ASSOCIATE_COMPLETION_PORT, link, 'job completion port')
    return job, port


def _resume(k32: ctypes.WinDLL, pid: int) -> None:
    """Resume every thread of the suspended *pid* (documented Toolhelp walk; it has one)."""
    snap = k32.CreateToolhelp32Snapshot(_SNAPTHREAD, 0)
    _check(snap and snap != wintypes.HANDLE(-1).value, 'CreateToolhelp32Snapshot')
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = k32.Thread32First(wintypes.HANDLE(snap), ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = k32.OpenThread(_THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID)
                _check(thread, 'OpenThread')
                k32.ResumeThread(wintypes.HANDLE(thread))
                k32.CloseHandle(wintypes.HANDLE(thread))
            more = k32.Thread32Next(wintypes.HANDLE(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))


def _assign(k32: ctypes.WinDLL, job: int, child: subprocess.Popen[bytes]) -> None:
    """Put the still-suspended *child* into *job*; kill it if that fails, so nothing runs unenforced."""
    handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, 0, child.pid)
    try:
        _check(handle and k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)), 'assign')
    except OSError:
        child.kill()
        child.wait()
        raise
    finally:
        if handle:
            k32.CloseHandle(wintypes.HANDLE(handle))


def _memory_message(k32: ctypes.WinDLL, port: int, wait_s: float) -> bool:
    """Drain the job's port (waiting up to *wait_s* seconds for the first message): did the memory limit fire?"""
    code, key, overlapped = wintypes.DWORD(), ctypes.c_void_p(), ctypes.c_void_p()
    hit = False
    while k32.GetQueuedCompletionStatus(
        port, ctypes.byref(code), ctypes.byref(key), ctypes.byref(overlapped), int(wait_s * 1000)
    ):
        hit = hit or code.value == _MSG_JOB_MEMORY_LIMIT
        wait_s = 0.0
    return hit


def _measure(k32: ctypes.WinDLL, job: int) -> tuple[int, float]:
    limits, acct = _Extended(), _Accounting()
    k32.QueryInformationJobObject(
        wintypes.HANDLE(job), _EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits), None
    )
    k32.QueryInformationJobObject(
        wintypes.HANDLE(job), _BASIC_ACCOUNTING, ctypes.byref(acct), ctypes.sizeof(acct), None
    )
    return int(limits.PeakJobMemoryUsed), (acct.TotalUserTime + acct.TotalKernelTime) / 1e7


def contain(
    argv: Sequence[str], demand: Demand, cwd: str | None, env: Mapping[str, str] | None, tag: tuple[str, str | None]
) -> Outcome:
    """Run *argv* inside a job sized to *demand*; wait, enforce, measure, and close the job."""
    k32 = _k32()
    job, port = _job(k32, demand)
    start = time.monotonic()
    try:
        child = subprocess.Popen(
            list(argv), cwd=cwd, env=None if env is None else dict(env), creationflags=_CREATE_SUSPENDED
        )
        _assign(k32, job, child)
        _resume(k32, child.pid)
        reason = _supervise(k32, job, port, child, start + demand.wall_s)
        peak, cpu_s = _measure(k32, job)
        returncode = child.returncode if child.returncode is not None else child.wait()
        return Outcome(returncode, reason, peak, cpu_s, time.monotonic() - start, 'job-object', *tag)
    finally:
        k32.CloseHandle(wintypes.HANDLE(job))
        k32.CloseHandle(wintypes.HANDLE(port))


def _supervise(k32: ctypes.WinDLL, job: int, port: int, child: subprocess.Popen[bytes], deadline: float) -> str:
    """Wait for *child*; terminate the whole job at a memory-limit message or at *deadline*.

    A child that hit the limit and exited on its own (a failed allocation it handled by dying) is a
    ``memory`` ending too: the message is still on the port when it is gone.
    """
    while child.poll() is None:
        left_s = deadline - time.monotonic()
        if left_s <= 0:
            reason = 'wall'
        elif _memory_message(k32, port, min(_TICK_S, left_s)):
            reason = 'memory'
        else:
            continue
        k32.TerminateJobObject(wintypes.HANDLE(job), wintypes.UINT(_KILLED))
        child.wait()
        return reason
    return 'memory' if _memory_message(k32, port, 0) else 'exit'
