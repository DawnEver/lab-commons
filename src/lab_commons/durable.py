"""Tier 1 -- STATE FILES ARE REPLACED WHOLE, AND A TORN ONE NEVER KILLS A RUN.

INCIDENT 2026-10-20: the box's Python was deleted at 02:02 mid-run. ``compute-learned/<repo>.json``, last
rewritten at 02:01:08 by a bare ``write_text`` (open with O_TRUNC, then write), was left as 5746 bytes of NUL,
and every ``learned()`` raised ``JSONDecodeError``: every run on the box died at admission until a person
found the file.

* :func:`durable_write` -- the ONE way lab-commons rewrites a state file: a temp file in the same directory,
  flushed and fsynced, then ``os.replace``. A crash at any point leaves the whole old file or the whole new
  one; a failed write removes its temp.
* :func:`read_table` -- a JSON table that cannot be read is QUARANTINED (``<name>.corrupt-<stamp>``, kept as
  evidence), warned about loudly naming its path, and read as ``{}``. Such a table is regenerable; a dead
  box is not.

Stdlib only; any layer may import it.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from lab_commons.log import emit

__all__ = ['durable_text', 'durable_write', 'read_table']


def _fsync_directory(directory: Path) -> None:
    if os.name == 'nt':  # Windows opens no directory handle; NTFS journals the rename itself
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def durable_write(target: Path, data: bytes) -> Path:
    """Write *data* to *target* so a crash leaves the whole file or the previous one: temp, fsync, replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f'{target.name}.{os.getpid()}.tmp')
    try:
        with staged.open('wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise
    _fsync_directory(target.parent)
    return target


def durable_text(target: Path, text: str) -> Path:
    """:func:`durable_write` of *text* as UTF-8."""
    return durable_write(target, text.encode('utf-8'))


def read_table(path: Path) -> dict[str, Any]:
    """The JSON object at *path*; ``{}`` when absent, and ``{}`` -- quarantined and warned -- when unreadable."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        table = json.loads(raw.decode('utf-8'))
        if isinstance(table, dict):
            return table
        why = f'a {type(table).__name__}, not a table'
    except (UnicodeDecodeError, ValueError) as failed:
        why = f'{type(failed).__name__}: {failed}'
    quarantine = path.with_name(f'{path.name}.corrupt-{time.strftime("%Y%m%dT%H%M%S")}-{os.getpid()}')
    with contextlib.suppress(OSError):
        path.replace(quarantine)
    emit(
        f'[lab_commons] UNREADABLE state file {path} ({len(raw)} bytes; {why}) -- moved to {quarantine.name} and '
        f'read as empty; it is regenerated as runs record again',
        err=True,
    )
    return {}
