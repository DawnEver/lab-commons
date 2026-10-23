"""A DURABLE WRITE -- a crash leaves the whole file or the previous one, never half.

Temp name, fsync, rename over the target, and the directory fsynced (POSIX). The compute journal and
result store write through it, and so does the forge's outbox (which builds on this side, never the reverse).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Final

__all__ = ['REPLACE_RETRY_S', 'durable_write']


def _fsync_directory(directory: Path) -> None:
    if os.name == 'nt':  # Windows opens no directory handle; NTFS journals the rename itself
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


REPLACE_RETRY_S: Final = 2.0
"""How long a rename waits out a Windows reader holding the target open (seconds)."""


def durable_write(target: Path, data: bytes) -> Path:
    """Write *data* to *target* so a crash or a concurrent reader sees the whole file or the previous one.

    Temp (named per process AND thread, so two writers never share one), fsync, rename. On Windows a rename
    onto a file another process holds open raises ``PermissionError``; it is retried for
    :data:`REPLACE_RETRY_S`, then raised.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f'{target.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with staged.open('wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    deadline = time.monotonic() + REPLACE_RETRY_S
    while True:
        try:
            staged.replace(target)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                staged.unlink(missing_ok=True)
                raise
            time.sleep(0.01)
    _fsync_directory(target.parent)
    return target
