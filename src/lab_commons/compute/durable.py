"""A DURABLE WRITE -- a crash leaves the whole file or the previous one, never half.

Temp name, fsync, rename over the target, and the directory fsynced (POSIX). The compute journal and
result store write through it, and so does the forge's outbox (which builds on this side, never the reverse).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ['durable_write']


def _fsync_directory(directory: Path) -> None:
    if os.name == 'nt':  # Windows opens no directory handle; NTFS journals the rename itself
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def durable_write(target: Path, data: bytes) -> Path:
    """Write *data* to *target* so a crash leaves the whole file or the previous one: temp, fsync, rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f'{target.name}.{os.getpid()}.tmp')
    with staged.open('wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    staged.replace(target)
    _fsync_directory(target.parent)
    return target
