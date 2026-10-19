"""Tier 1 -- THE PROCESS CENSUS: every live process, with what it executes, how it was invoked and how old it is.

ONE READER OF THE PROCESS TABLE FOR "WHO IS RUNNING WHAT". Two questions need it and neither may need the
process's cooperation: is a run live in this checkout (:mod:`lab_commons.dev.liverun`), and which run is this
pid (:mod:`lab_commons.dev.stoprun`). Before 2026-10-19 the second one shelled out to ``Get-CimInstance``
(about a second per read), which no ``PreToolUse`` hook can pay on every tool call; the Win32 calls below
answer in milliseconds.

WHAT EACH FIELD IS, READ, never guessed:

* ``executable`` -- the path the process executes FROM: ``QueryFullProcessImageNameW`` on Windows (a venv's
  ``python.exe`` launcher stays alive for its child's whole life, so its image names the venv); on Linux
  ``argv[0]`` made absolute against ``/proc/<pid>/cwd``, falling back to ``/proc/<pid>/exe`` -- a venv's
  ``python`` is a SYMLINK and the kernel's ``exe`` resolves through it to the base interpreter, losing the
  venv. ``''`` when it cannot be read (another user's process, a protected one, macOS). An unreadable
  executable is NOT evidence of anything, so a reader must not treat it as a match.
* ``command_line`` -- ``NtQueryInformationProcess(ProcessCommandLineInformation)`` on Windows (8.1 and
  later, with only ``PROCESS_QUERY_LIMITED_INFORMATION``), ``/proc/<pid>/cmdline`` on Linux, ``ps`` on macOS.
* ``age_s`` -- seconds since the process was created, ``None`` when unreadable.

Stdlib only (``ctypes``), and it never imports ``lab_commons.dev``: a cluster-side reader may use it.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons import proc

__all__ = ['Process', 'processes']

_QUERY_LIMITED: Final = 0x1000
_COMMAND_LINE_CLASS: Final = 60  # ProcessCommandLineInformation
_FILETIME_PER_S: Final = 10_000_000


@dataclass(frozen=True, slots=True)
class Process:
    """One live process, as the platform's own table reports it."""

    pid: int
    ppid: int
    executable: str
    command_line: str
    age_s: float | None


def processes() -> tuple[Process, ...]:
    """Every process this user can see, from ONE snapshot; ``()`` when the table cannot be read."""
    if os.name == 'nt':
        return _windows()
    if sys.platform.startswith('linux'):
        return _linux()
    return _ps()


class _UnicodeString(ctypes.Structure):
    _fields_ = (('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort), ('Buffer', ctypes.c_void_p))


def _windows() -> tuple[Process, ...]:
    rows = proc._process_rows() or {}  # noqa: SLF001 -- the one Toolhelp32 snapshot, read in its own package
    kernel32 = ctypes.WinDLL('kernel32')
    ntdll = ctypes.WinDLL('ntdll')
    now = ctypes.c_ulonglong()
    kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
    out = []
    for pid, (_image, ppid) in rows.items():
        handle = kernel32.OpenProcess(_QUERY_LIMITED, False, pid) if pid else 0  # noqa: FBT003 -- Win32 positional
        if not handle:
            out.append(Process(pid, ppid, '', '', None))
            continue
        try:
            out.append(
                Process(
                    pid,
                    ppid,
                    _image_name(kernel32, handle),
                    _command_line(ntdll, handle),
                    _age(kernel32, handle, now.value),
                )
            )
        finally:
            kernel32.CloseHandle(handle)
    return tuple(out)


def _image_name(kernel32: ctypes.WinDLL, handle: int) -> str:
    size = ctypes.c_ulong(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    return buffer.value if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''


def _command_line(ntdll: ctypes.WinDLL, handle: int) -> str:
    needed = ctypes.c_ulong(0)
    ntdll.NtQueryInformationProcess(handle, _COMMAND_LINE_CLASS, None, 0, ctypes.byref(needed))
    if not needed.value:
        return ''
    buffer = ctypes.create_string_buffer(needed.value)
    status = ntdll.NtQueryInformationProcess(handle, _COMMAND_LINE_CLASS, buffer, needed, ctypes.byref(needed))
    if status != 0:
        return ''
    header = _UnicodeString.from_buffer(buffer)
    return ctypes.wstring_at(header.Buffer, header.Length // 2) if header.Buffer else ''


def _age(kernel32: ctypes.WinDLL, handle: int, now: int) -> float | None:
    created, exited, kernel_t, user_t = (ctypes.c_ulonglong() for _ in range(4))
    stamps = (ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_t), ctypes.byref(user_t))
    if not kernel32.GetProcessTimes(handle, *stamps):
        return None
    return max(0.0, (now - created.value) / _FILETIME_PER_S)


def _linux() -> tuple[Process, ...]:
    rows = proc._process_rows() or {}  # noqa: SLF001 -- the one /proc reader, in its own package
    try:
        uptime = float(Path('/proc/uptime').read_text(encoding='utf-8').split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    ticks = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
    out = []
    for pid, (_name, ppid) in rows.items():
        base = Path(f'/proc/{pid}')
        try:
            argv = (base / 'cmdline').read_bytes().decode(errors='replace').split('\0')
        except OSError:
            argv = []
        command_line = ' '.join(word for word in argv if word)
        out.append(
            Process(pid, ppid, _invoked(base, argv[0] if argv else ''), command_line, _age_linux(pid, uptime, ticks))
        )
    return tuple(out)


def _invoked(base: Path, argv0: str) -> str:
    """The path a Linux process was started from, symlinks KEPT (a venv's interpreter is one)."""
    try:
        return os.path.normpath((base / 'cwd').readlink() / argv0) if '/' in argv0 else str((base / 'exe').readlink())
    except OSError:
        return ''


def _age_linux(pid: int, uptime: float | None, ticks: int) -> float | None:
    started = proc._creation_time(pid)  # noqa: SLF001 -- starttime in clock ticks since boot
    return None if uptime is None or started is None else max(0.0, uptime - started / ticks)


def _ps() -> tuple[Process, ...]:
    """macOS: ``ps`` reports pid, ppid, elapsed seconds and the command line; the image stays unread."""
    done = _door.run(
        [shutil.which('ps') or 'ps', '-ww', '-A', '-o', 'pid=,ppid=,etime=,args='],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
        timeout=60,
    )
    out = []
    for line in done.stdout.splitlines():
        pid, ppid, elapsed, *args = [*line.split(None, 3), '', '', ''][:4]
        if pid.isdigit() and ppid.isdigit():
            out.append(Process(int(pid), int(ppid), '', ''.join(args), _etime(elapsed)))
    return tuple(out)


def _etime(text: str) -> float | None:
    """``[[dd-]hh:]mm:ss`` -> seconds."""
    days, _, clock = text.rpartition('-')
    try:
        parts = [int(part) for part in clock.split(':')]
        seconds = sum(value * 60**power for power, value in enumerate(reversed(parts)))
        return float(seconds + int(days or 0) * 86400)
    except ValueError:
        return None
