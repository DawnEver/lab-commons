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
from typing import Final

import lab_commons.launch as _door

#: ``ps -o lstart=`` in the C locale, e.g. ``Thu Oct 30 10:11:12 2026`` (local time).
_LSTART: Final = '%a %b %d %H:%M:%S %Y'


def _tool(*argv: str) -> str | None:
    """*argv*'s stdout, or ``None`` when the tool is absent or fails."""
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    try:
        done = _door.run(
            [exe, *argv[1:]],
            env={**os.environ, 'LC_ALL': 'C'},  # one spelling of dates and numbers, whatever the user's locale
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=30,
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


def is_zombie(pid: int) -> bool:
    """Whether *pid* has exited and awaits only its parent's ``wait``: its ``ps -o stat=`` starts with ``Z``.

    The darwin sibling of :func:`lab_commons._proc_linux.is_zombie`; unreadable is ``False``, erring towards alive.
    """
    return (_tool('ps', '-o', 'stat=', '-p', str(pid)) or '').strip().startswith('Z')


def parse_lstart(text: str) -> int | None:
    """One ``ps -o lstart=`` stamp -> epoch seconds, or ``None`` when it does not parse."""
    try:
        return int(time.mktime(time.strptime(' '.join(text.split()), _LSTART)))
    except (ValueError, OverflowError):
        return None


def start_time(pid: int) -> int | None:
    """*pid*'s start, epoch seconds from ``ps -o lstart=``, or ``None`` when it cannot be read.

    Whole seconds: a child started in its parent's second carries an EQUAL stamp, which the edge
    refutation keeps; a recycled pid still starts later than the process it replaced.
    """
    return parse_lstart(_tool('ps', '-o', 'lstart=', '-p', str(pid)) or '')


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
