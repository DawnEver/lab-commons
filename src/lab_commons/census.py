"""Tier 1 -- THE PROCESS CENSUS: every live process, with what it executes, how it was invoked and how old it is.

ONE READER OF THE PROCESS TABLE FOR "WHO IS RUNNING WHAT". Two questions need it and neither may need the
process's cooperation: is a run live in this checkout (dev-lab's live-run guard), and which run is this
pid (dev-lab's ``stoprun``). Before 2026-10-19 the second one shelled out to ``Get-CimInstance``
(about a second per read), which no ``PreToolUse`` hook can pay on every tool call; the Win32 calls below
answer in milliseconds.

WHAT EACH FIELD IS, READ, never guessed:

* ``executable`` -- the path the process executes FROM: ``QueryFullProcessImageNameW`` on Windows (a venv's
  ``python.exe`` launcher stays alive for its child's whole life, so its image names the venv); on Linux
  ``argv[0]`` made absolute against ``/proc/<pid>/cwd``, falling back to ``/proc/<pid>/exe`` -- a venv's
  ``python`` is a SYMLINK and the kernel's ``exe`` resolves through it to the base interpreter, losing the
  venv; on macOS the same ``argv[0]`` rule against the ``lsof`` cwd. ``''`` when it cannot be read (another
  user's process, a protected one, a bare name on macOS). An unreadable
  executable is NOT evidence of anything, so a reader must not treat it as a match.
* ``command_line`` -- ``NtQueryInformationProcess(ProcessCommandLineInformation)`` on Windows (8.1 and
  later, with only ``PROCESS_QUERY_LIMITED_INFORMATION``), ``/proc/<pid>/cmdline`` on Linux, ``ps`` on macOS.
* ``age_s`` -- seconds since the process was created, ``None`` when unreadable.

Stdlib only (``ctypes``), and it never imports dev-lab: a cluster-side reader may use it.
"""

from __future__ import annotations

import ctypes
import os
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lab_commons import _proc_darwin, proc

__all__ = ['Process', 'processes']

_QUERY_LIMITED: Final = 0x1000
_QUERY_AND_READ: Final = 0x0400 | 0x0010  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
_PEB_PARAMETERS: Final = 0x20  # PEB.ProcessParameters, 64-bit
_PARAMETERS_CWD: Final = 0x38  # RTL_USER_PROCESS_PARAMETERS.CurrentDirectory.DosPath, 64-bit
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
    #: The working directory, ``''`` when unreadable: the PEB (Windows), ``/proc/<pid>/cwd`` (Linux), ``lsof`` (macOS).
    cwd: str = ''


def processes() -> tuple[Process, ...]:
    """Every process this user can see, from ONE snapshot; ``()`` when the table cannot be read."""
    if os.name == 'nt':
        return _windows()
    if sys.platform.startswith('linux'):
        return _linux()
    return _darwin()


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
                    _cwd(kernel32, ntdll, pid),
                )
            )
        finally:
            kernel32.CloseHandle(handle)
    return tuple(out)


def _read(kernel32: ctypes.WinDLL, handle: int, address: int, size: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(size)
    done = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(done))
    return buffer.raw if ok and done.value == size else None


def _cwd(kernel32: ctypes.WinDLL, ntdll: ctypes.WinDLL, pid: int) -> str:
    """The working directory out of the process's own PEB (64-bit layout); ``''`` when it cannot be read."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:  # noqa: PLR2004 -- the offsets below are the 64-bit layout
        return ''
    handle = kernel32.OpenProcess(_QUERY_AND_READ, False, pid)  # noqa: FBT003 -- Win32 positional
    if not handle:
        return ''
    try:
        basic = (ctypes.c_void_p * 6)()
        if ntdll.NtQueryInformationProcess(handle, 0, basic, ctypes.sizeof(basic), None) != 0 or not basic[1]:
            return ''
        params = _read(kernel32, handle, basic[1] + _PEB_PARAMETERS, 8)
        if params is None:
            return ''
        header = _read(kernel32, handle, int.from_bytes(params, 'little') + _PARAMETERS_CWD, 16)
        if header is None:
            return ''
        length, address = int.from_bytes(header[:2], 'little'), int.from_bytes(header[8:], 'little')
        text = _read(kernel32, handle, address, length) if length and address else None
        return text.decode('utf-16-le') if text else ''
    finally:
        kernel32.CloseHandle(handle)


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
    ticks = os.sysconf('SC_CLK_TCK')  # Linux only: this reader runs nowhere else
    out = []
    for pid, (_name, ppid) in rows.items():
        base = Path(f'/proc/{pid}')
        try:
            argv = (base / 'cmdline').read_bytes().decode(errors='replace').split('\0')
        except OSError:
            argv = []
        command_line = ' '.join(word for word in argv if word)
        try:
            cwd = str((base / 'cwd').readlink())
        except OSError:
            cwd = ''
        invoked = _invoked(base, argv[0] if argv else '')
        out.append(Process(pid, ppid, invoked, command_line, _age_linux(pid, uptime, ticks), cwd))
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


def _darwin() -> tuple[Process, ...]:
    """macOS: one ``ps`` snapshot (pid, ppid, elapsed, command line) and one ``lsof`` for every cwd."""
    table = _proc_darwin._tool('ps', '-ww', '-A', '-o', 'pid=,ppid=,etime=,args=')  # noqa: SLF001 -- the one tool runner
    if table is None:
        return ()
    cwds = _lsof_cwds(_proc_darwin._tool('lsof', '-a', '-d', 'cwd', '-F', 'pn') or '')  # noqa: SLF001
    out = []
    for line in table.splitlines():
        pid, ppid, elapsed, args = [*line.split(None, 3), '', '', ''][:4]
        if pid.isdigit() and ppid.isdigit():
            cwd = cwds.get(int(pid), '')
            out.append(Process(int(pid), int(ppid), _invoked_posix(cwd, args), args, _etime(elapsed), cwd))
    return tuple(out)


def _lsof_cwds(text: str) -> dict[int, str]:
    """``lsof -F pn`` field lines (``p<pid>`` then ``n<path>``) -> ``{pid: cwd}``."""
    cwds: dict[int, str] = {}
    pid = None
    for line in text.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('n') and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def _invoked_posix(cwd: str, args: str) -> str:
    """``argv[0]`` made absolute against *cwd*, symlinks KEPT; ``''`` when no path names it."""
    argv0 = args.split(None, 1)[0] if args.strip() else ''
    if argv0.startswith('/'):
        return posixpath.normpath(argv0)
    return posixpath.normpath(posixpath.join(cwd, argv0)) if '/' in argv0 and cwd else ''


def _etime(text: str) -> float | None:
    """``[[dd-]hh:]mm:ss`` -> seconds."""
    days, _, clock = text.rpartition('-')
    try:
        parts = [int(part) for part in clock.split(':')]
        seconds = sum(value * 60**power for power, value in enumerate(reversed(parts)))
        return float(seconds + int(days or 0) * 86400)
    except ValueError:
        return None
