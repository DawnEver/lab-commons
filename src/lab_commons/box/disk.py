"""DISK IS SUPPLY TOO: the box refuses a run whose volumes cannot keep the machine's free-space floor (D54).

The box admits on cpu, memory, seats and devices; a run also WRITES, and a full volume blocks every session that
shares it (WS2, 2026-11-01: D: down to 0.1 MB, nothing warned). The bound is the ONE machine floor every cache
already keeps (:func:`lab_commons.storage.free_floor`, ``[storage] free_floor_gb``, default 50 GiB; capped by a
declared quota, :mod:`lab_commons.headroom`) -- never a second constant.

:func:`verdict` measures the volumes a run writes (:func:`volumes`: the box ledger's and the working directory's,
one per device) and answers ``(refusal, warning)``:

* below the floor -> a refusal naming the volume, its free space and the floor, and the doors that free space;
* below the WATERMARK -- twice the floor, derived so the machine declares one fact -- a warning, still admitted;
* else nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final

from lab_commons.compute.floor import measure
from lab_commons.headroom import Floor

__all__ = ['WATERMARK', 'verdict', 'volumes']

#: The warning starts at this multiple of the floor: one declared fact, the watermark derived from it.
WATERMARK: Final = 2

_GIB: Final = 1024**3

#: What frees space on a development machine, named in every refusal and warning.
_DOORS: Final = (
    'free space there (stale worktree venvs, scratch trees and regenerable output are the usual holders), '
    'or lower [storage] free_floor_gb in the machine file (lab_commons.storage)'
)


def volumes(paths: Iterable[Path]) -> list[Path]:
    """*paths* reduced to one existing path per device, in order: each volume measured once."""
    seen: set[int] = set()
    found: list[Path] = []
    for path in paths:
        here = next((one for one in (path, *path.parents) if one.exists()), None)
        if here is None:
            continue
        device = here.stat().st_dev
        if device not in seen:
            seen.add(device)
            found.append(here)
    return found


def _gib(count: int) -> str:
    return f'{count / _GIB:.1f} GiB'


def verdict(paths: Iterable[Path], floor: Floor) -> tuple[str | None, str | None]:
    """``(refusal, warning)`` for a run writing on the volumes of *paths* under *floor*; each ``None`` when clear."""
    warnings: list[str] = []
    for volume in volumes(paths):
        free, files = measure(volume, floor)
        if free < floor.bytes or (files is not None and files[1] < files[0]):
            counted = f', {files[1]} files left of a {files[0]}-file floor' if files else ''
            return f'{volume} has {_gib(free)} free{counted}, below the floor {_gib(floor.bytes)} -- {_DOORS}', None
        if free < WATERMARK * floor.bytes:
            warnings.append(f'{volume} has {_gib(free)} free, under the watermark {_gib(WATERMARK * floor.bytes)}')
    return None, ('; '.join(warnings) + f' -- {_DOORS}') if warnings else None
