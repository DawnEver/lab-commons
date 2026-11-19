"""THE FREE-SPACE FLOOR -- the one bound on every regenerable cache: its volume keeps ``floor`` bytes free (D54).

A cache is bounded by what its DISK can spare, not by a fixed size: the same 200 GB is nothing on a 4 TB volume
and everything on a 256 GB one. So after each write a cache calls :func:`keep`, which measures the free space of
the volume holding it (:func:`free_bytes`, ``shutil.disk_usage``) and, while it is under the floor, evicts the
least recently hit entry -- oldest stamp first, never the entry just written -- re-measuring after each. If every
other entry is gone and the floor still does not hold, the write is refused by name (:class:`BelowFloor`).

The door owns no cache layout: the caller hands its live entries as ``(stamp, path, bytes)`` and its own
``evict``, so the result store and a deck cache share one rule. The floor is the caller's -- the machine's
``[storage] free_floor_gb`` (:func:`lab_commons.storage.free_floor_bytes`) -- never a value in this module.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

__all__ = ['BelowFloor', 'free_bytes', 'keep']


class BelowFloor(OSError):
    """A write refused: its volume cannot keep the free-space floor even with every other entry evicted."""

    def __init__(self, volume: Path, floor: int, free: int) -> None:
        """Name *volume*, its *floor* and its *free* bytes after every eviction."""
        self.volume, self.floor, self.free = volume, floor, free
        msg = (
            f'{volume}: the volume keeps a free-space floor {floor} bytes but has free {free} bytes with every '
            'other cache entry evicted -- free space on that volume, or lower [storage] free_floor_gb in the '
            'machine file (lab_commons.storage)'
        )
        super().__init__(msg)


def free_bytes(volume: Path) -> int:
    """The free bytes of the volume holding *volume*."""
    return shutil.disk_usage(volume).free


def keep(
    volume: Path,
    live: Iterable[tuple[int, Path, int]],
    floor: int,
    *,
    evict: Callable[[Path], object],
    keep: Path | None = None,
) -> list[Path]:
    """Evict *live* entries, least recently hit first, until *volume* keeps *floor* bytes free; return the evicted.

    *live* holds ``(stamp, path, bytes)``; *keep* (the entry just written) is never evicted.

    Raises:
        BelowFloor: the floor still does not hold with every candidate evicted.

    """
    gone: list[Path] = []
    free = free_bytes(volume)
    for _, path, _ in sorted(live, key=lambda entry: entry[0]):
        if free >= floor:
            return gone
        if path == keep:
            continue
        evict(path)
        gone.append(path)
        free = free_bytes(volume)
    if free < floor:
        raise BelowFloor(volume, floor, free)
    return gone
