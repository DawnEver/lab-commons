"""The macOS half of :mod:`lab_commons.proc`: neither ``kernel32`` nor ``/proc`` exists there.

Until 2026-10-10 every ``proc`` reader answered NOTHING on macOS -- an empty process tree, so the
bounded wall could not reap and the reaper could not protect its own lineage -- the unreadable-
machine shape ``proc`` names as its defect. Stdlib only, through the platform's own tools
(``sysctl``, ``vm_stat``, ``ps``); returns primitives so ``proc`` keeps its one ``SystemMemory``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

import lab_commons.launch as _door


def _tool(*argv: str) -> str | None:
    """*argv*'s stdout under the C locale (``ps`` dates are locale-formatted), or ``None`` when absent or failed."""
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    try:
        done = _door.run(
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=30,
            env={**os.environ, 'LC_ALL': 'C'},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def parse_vm_stat(text: str, *, total_bytes: int) -> tuple[int, int] | None:
    """``vm_stat`` -> ``(total, available)``, available = (free + inactive) pages, as psutil reads it.

    No page size, no reading: guessing 4 KiB on Apple silicon (16 KiB pages) is a 4x error.
    """
    size = re.search(r'page size of (\d+) bytes', text)
    pages = {key.strip(): int(value) for key, value in re.findall(r'^Pages ([a-z ]+):\s+(\d+)\.', text, re.MULTILINE)}
    if size is None or total_bytes <= 0 or 'free' not in pages or 'inactive' not in pages:
        return None
    return total_bytes, min((pages['free'] + pages['inactive']) * int(size.group(1)), total_bytes)


def memory() -> tuple[int, int] | None:
    """``(total, available)`` bytes, or ``None`` when unreadable."""
    total = (_tool('sysctl', '-n', 'hw.memsize') or '').strip()
    text = _tool('vm_stat')
    if not total.isdigit() or text is None:
        return None
    return parse_vm_stat(text, total_bytes=int(total))


def rss_bytes(pid: int) -> int | None:
    """Resident bytes of *pid* from ``ps -o rss=`` (KiB), or ``None``."""
    rss = (_tool('ps', '-o', 'rss=', '-p', str(pid)) or '').strip()
    return int(rss) * 1024 if rss.isdigit() else None


def parse_ps_rows(text: str) -> dict[int, tuple[str, int]]:
    """``ps -o pid=,ppid=,comm=`` lines -> ``{pid: (image, parent_pid)}``; a malformed line is skipped."""
    rows: dict[int, tuple[str, int]] = {}
    for line in text.splitlines():
        pid, ppid, *name = [*line.split(None, 2), '', ''][:3]
        if pid.isdigit() and ppid.isdigit():
            rows[int(pid)] = (name[0], int(ppid))
    return rows


def process_rows() -> dict[int, tuple[str, int]] | None:
    """``{pid: (image, parent_pid)}`` from one ``ps`` snapshot, or ``None`` when unreadable."""
    text = _tool('ps', '-A', '-o', 'pid=,ppid=,comm=')
    return None if text is None else (parse_ps_rows(text) or None)


#: ``ps -o stat=,lstart=`` words: the state letters, then ``lstart``'s weekday, month, day, time and year.
_STATE_WORDS = 6


def parse_ps_state(text: str) -> tuple[str, int] | None:
    """``ps -o stat=,lstart=`` -> ``(state letters, start in epoch seconds)``, or ``None`` when malformed.

    ``lstart`` is the C-locale ``Thu Oct 30 04:28:40 2026``: whole seconds, the same value on every read,
    so equality identifies the process and a recycled pid reads a later stamp.
    """
    fields = text.split()
    if len(fields) != _STATE_WORDS:
        return None
    try:
        started = time.mktime(time.strptime(' '.join(fields[1:]), '%a %b %d %H:%M:%S %Y'))
    except (ValueError, OverflowError):
        return None
    return fields[0], int(started)


def _state(pid: int) -> tuple[str, int] | None:
    return parse_ps_state(_tool('ps', '-o', 'stat=,lstart=', '-p', str(pid)) or '')


def is_zombie(pid: int) -> bool:
    """Whether *pid* has exited and awaits its parent's ``wait`` (``ps`` state ``Z``); ``False`` when unread.

    The darwin twin of :func:`lab_commons._proc_linux.is_zombie`: signal 0 answers yes for a zombie, so
    without this every killed direct child read as a survivor until its ``Popen`` reaped it.
    """
    state = _state(pid)
    return state is not None and state[0].startswith('Z')


def start_time(pid: int) -> int | None:
    """*pid*'s start in epoch seconds from ``ps -o lstart=``, or ``None`` when unreadable."""
    state = _state(pid)
    return None if state is None else state[1]
