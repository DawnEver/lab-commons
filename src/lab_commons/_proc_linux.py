"""The Linux half of :mod:`lab_commons.proc`: every reading comes from ``/proc``.

The sibling of :mod:`lab_commons._proc_darwin`, split out of ``proc`` on 2026-10-19 when its code
crossed the module-size band. Stdlib only; returns primitives so ``proc`` keeps its one
``SystemMemory``. Each reader answers ``None`` (or ``False``) where ``/proc`` cannot be read.
"""

from __future__ import annotations

from pathlib import Path

#: A ``/proc/meminfo`` or ``/proc/<pid>/status`` line in kB is exactly ``<value> kB`` -- two tokens.
_KB_LINE_TOKENS = 2

#: ``starttime`` is field 22 of ``/proc/<pid>/stat``; counted after the ``)`` closing the name (field 2),
#: whose next field is 3, it sits at index 19.
_STARTTIME_AFTER_NAME = 22 - 3


def _kb_fields(text: str) -> dict[str, int]:
    """``{key: bytes}`` for every ``key: <value> kB`` line of *text*."""
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(':')
        parts = rest.split()
        if len(parts) == _KB_LINE_TOKENS and parts[1] == 'kB':
            fields[key.strip()] = int(parts[0]) * 1024
    return fields


def memory() -> tuple[int, int] | None:
    """``(total, available)`` bytes from ``/proc/meminfo``. ``MemAvailable``, NOT ``MemFree``.

    ``MemFree`` excludes the page cache, which the kernel hands back on demand, so a healthy Linux
    box with a warm cache reports single-digit ``MemFree`` and would size every pool to one worker.
    ``MemAvailable`` is the kernel's own estimate of what a new allocation can actually get, and a
    kernel too old to publish it is reported UNREADABLE rather than approximated -- the fallback
    would be exactly the silent-degradation shape ``proc`` exists to avoid.
    """
    try:
        fields = _kb_fields(Path('/proc/meminfo').read_text(encoding='utf-8'))
    except OSError:
        return None
    total, available = fields.get('MemTotal'), fields.get('MemAvailable')
    if total is None or available is None or total <= 0:
        return None
    return total, available


def rss_bytes(pid: int) -> int | None:
    """``VmRSS`` from ``/proc/<pid>/status`` -- the POSIX half, so the ceiling is enforceable there too."""
    try:
        return _kb_fields(Path(f'/proc/{pid}/status').read_text(encoding='utf-8')).get('VmRSS')
    except OSError:
        return None


def is_zombie(pid: int) -> bool:
    """Whether *pid* has exited and awaits only its parent's ``wait`` -- not RUNNING, though signal 0 says yes.

    A killed direct child of ours stays a zombie until its ``Popen`` reaps it, so without this a
    waiter would mistake every killed child for a survivor. Where ``/proc`` cannot be read the answer
    is ``False``, which errs towards "alive" and never reports a live process dead.
    """
    try:
        text = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
    except OSError:
        return False
    fields = text.rpartition(')')[2].split()
    return bool(fields) and fields[0] in {'Z', 'X'}


def process_rows() -> dict[int, tuple[str, int]] | None:
    """``{pid: (name, parent_pid)}`` from every ``/proc/<pid>/status``, or ``None`` when unreadable."""
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


def stat_start_time(text: str) -> int | None:
    """``starttime`` from one ``/proc/<pid>/stat`` line, split after the LAST ``)`` since a name may hold one."""
    _, paren, rest = text.rpartition(')')
    fields = rest.split()
    if not paren or len(fields) <= _STARTTIME_AFTER_NAME or not fields[_STARTTIME_AFTER_NAME].isdigit():
        return None
    return int(fields[_STARTTIME_AFTER_NAME])


def start_time(pid: int) -> int | None:
    """*pid*'s ``starttime`` (clock ticks since boot), or ``None`` when it cannot be read."""
    try:
        return stat_start_time(Path(f'/proc/{pid}/stat').read_text(encoding='utf-8'))
    except OSError:
        return None
