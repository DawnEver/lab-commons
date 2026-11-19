"""THE FREE-SPACE FLOOR -- the one bound on every regenerable cache: its volume keeps ``floor`` bytes free (D54).

A cache is bounded by what its DISK can spare, not by a fixed size: the same 200 GB is nothing on a 4 TB volume
and everything on a 256 GB one. So after each write a cache calls :func:`keep`, which measures the free space of
the volume holding it (:func:`free_bytes`, ``shutil.disk_usage``) and, while it is under the floor, evicts the
least recently hit entry -- oldest stamp first, never the entry just written -- re-measuring after each. If every
other entry is gone and the floor still does not hold, the write is refused by name (:class:`BelowFloor`).

Where the machine declares a QUOTA (:class:`lab_commons.headroom.Quota`), the free space is the smaller of the
volume's and the quota's, and a quota that limits files must also keep the floor's files (1% of that limit when the
machine declares no ``free_floor_files``). An unreadable declared quota is refused, never replaced by the volume.

The door owns no cache layout: the caller hands its live entries as ``(stamp, path, bytes)`` and its own
``evict``, so the result store and a deck cache share one rule. The floor is the caller's -- the machine's
``[storage]`` floor (:func:`lab_commons.storage.free_floor`) -- never a value in this module.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from lab_commons.headroom import Floor, read

__all__ = ['BelowFloor', 'free_bytes', 'keep', 'measure']


class BelowFloor(OSError):
    """A write refused: its volume cannot keep the free-space floor even with every other entry evicted."""

    def __init__(self, volume: Path, floor: int, free: int, files: tuple[int, int] | None = None) -> None:
        """Name *volume*, its *floor* and *free* bytes, and *files* ``(floor, left)`` where a quota limits files."""
        self.volume, self.floor, self.free, self.files = volume, floor, free, files
        counted = f' and keeps {files[0]} files free but has {files[1]} left' if files else ''
        msg = (
            f'{volume}: the cache keeps a free-space floor {floor} bytes but has free {free} bytes{counted} with '
            'every other cache entry evicted -- free space there (volume or quota), or lower [storage] '
            'free_floor_gb / free_floor_files in the machine file (lab_commons.storage)'
        )
        super().__init__(msg)


def free_bytes(volume: Path) -> int:
    """The free bytes of the volume holding *volume*."""
    return shutil.disk_usage(volume).free


def measure(volume: Path, floor: Floor) -> tuple[int, tuple[int, int] | None]:
    """The free bytes (volume, capped by the quota) and ``(files floor, files left)`` where a quota limits files."""
    free = free_bytes(volume)
    if floor.quota is None:
        return free, None
    usage = read(floor.quota)
    if usage.bytes_left is not None:
        free = min(free, usage.bytes_left)
    if usage.files_left is None or usage.files_limit is None:
        return free, None
    files_floor = floor.files if floor.files is not None else math.ceil(usage.files_limit / 100)
    return free, (files_floor, usage.files_left)


def _holds(floor: Floor, free: int, files: tuple[int, int] | None) -> bool:
    return free >= floor.bytes and (files is None or files[1] >= files[0])


def keep(
    volume: Path,
    live: Iterable[tuple[int, Path, int]],
    floor: Floor,
    *,
    evict: Callable[[Path], object],
    keep: Path | None = None,
) -> list[Path]:
    """Evict *live* entries, least recently hit first, until *volume* keeps *floor*; return the evicted.

    *live* holds ``(stamp, path, bytes)``; *keep* (the entry just written) is never evicted.

    Raises:
        BelowFloor: the floor still does not hold with every candidate evicted.
        QuotaUnreadable: the floor's declared quota could not be read.

    """
    gone: list[Path] = []
    free, files = measure(volume, floor)
    for _, path, _ in sorted(live, key=lambda entry: entry[0]):
        if _holds(floor, free, files):
            return gone
        if path == keep:
            continue
        evict(path)
        gone.append(path)
        free, files = measure(volume, floor)
    if not _holds(floor, free, files):
        raise BelowFloor(volume, floor.bytes, free, files)
    return gone
