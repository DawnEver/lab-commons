"""Packing arranged islands into bands of shelves, then sliding physical islands over what drives them."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = ['MAX_ASPECT', 'Packed', 'pack_bands']

#: A shelf choice making the drawing more than this many times wider than tall (or taller than wide) is never
#: taken while another is not: the readable-aspect bound the disconnected-node placement tests pin.
MAX_ASPECT = 3
#: A wrapping band's target width, as a multiple of the side of the square holding its islands' area.
_WRAP_SPREAD = 1.6

_Positions = dict[str, tuple[float, float]]
#: ``(island-local positions, width, height)``.
_Island = tuple[_Positions, float, float]
#: ``(y, x, width, keys)`` of one placed island.
_Shelf = tuple[float, float, float, tuple[str, ...]]


@dataclass(frozen=True)
class Packed:
    positions: _Positions
    width: float
    height: float


def _shelved(islands: Sequence[_Island], target: float, gap: float) -> tuple[float, float]:
    """(width, height) of ``islands`` shelf-packed left to right, wrapping past ``target``."""
    x = y = shelf = width = 0.0
    for _, w, h in islands:
        if x > 0 and x + w > target:
            x, y, shelf = 0.0, y + shelf + gap, 0.0
        width, shelf, x = max(width, x + w), max(shelf, h), x + w + gap
    return width, y + shelf


def _targets(arranged: Mapping[str, Sequence[_Island]], gap: float, band_gap: float) -> dict[str, float]:
    """Each band's wrap width: at least the widest island of ANY band; a band may also take ONE row.

    The pair of targets giving the smaller drawing (area, readable aspect first) wins; ties keep the wrap.
    """
    widest = max((w for islands in arranged.values() for _p, w, _h in islands), default=0.0)
    targets = {}
    for section, islands in arranged.items():
        wrap = max(widest, math.sqrt(sum((w + gap) * (h + gap) for _p, w, h in islands)) * _WRAP_SPREAD)
        row = sum(w for _p, w, _h in islands) + gap * (len(islands) - 1)
        targets[section] = dict.fromkeys((wrap, max(wrap, row)))
    sections = list(arranged)

    def area(choice: tuple[float, ...]) -> tuple[bool, float]:
        bands = [_shelved(arranged[section], target, gap) for section, target in zip(sections, choice, strict=True)]
        width = max(w for w, _h in bands)
        height = sum(h for _w, h in bands) + band_gap * (len(bands) - 1)
        return (width > MAX_ASPECT * height or height > MAX_ASPECT * width, width * height)

    choices = [()]
    for section in sections:
        choices = [(*c, t) for c in choices for t in targets[section]]
    return dict(zip(sections, min(choices, key=area), strict=True)) if sections else {}


def _slide_row(
    row: list[tuple[float, float, float, tuple[str, ...]]],
    positions: _Positions,
    *,
    gap: float,
    margin: float,
    limit: float,
) -> None:
    row.sort(key=lambda r: (r[0], r[1]))
    moved, left = [], margin
    for target, _, width, _ in row:
        moved.append(max(left, target - width / 2))
        left = moved[-1] + width + gap
    right = limit
    for index in reversed(range(len(row))):
        moved[index] = min(moved[index], right - row[index][2])
        right = moved[index] - gap
    for (_, x, _, keys), at in zip(row, moved, strict=True):
        for k in keys:
            positions[k] = (positions[k][0] + at - x, positions[k][1])


def _over_their_drivers(
    positions: _Positions,
    shelves: Sequence[_Shelf],
    drivers: Mapping[str, list[float]],
    *,
    gap: float,
    margin: float,
    canvas_width: float,
) -> None:
    """Slide each physical island along its shelf to sit over the causal nodes linked to it.

    Cross-band links are the longest wires; shelf order ignores them. Islands keep their shelf row and never
    overlap (a left-to-right sweep in driver order), and the drawing never widens to shorten a wire.
    """
    rows = defaultdict(list)
    for y, x, width, keys in shelves:
        seen = [d for k in keys for d in drivers.get(k, ())]
        rows[y].append((median(seen) if seen else x + width / 2, x, width, keys))
    for row in rows.values():
        _slide_row(row, positions, gap=gap, margin=margin, limit=canvas_width - margin)


def pack_bands(
    arranged: Mapping[str, Sequence[_Island]],
    cross_links: Iterable[tuple[str, str]],
    *,
    gap: float,
    margin: float,
    band_gap: float,
) -> Packed:
    """Shelf-pack each non-empty band top to bottom in ``arranged`` order.

    ``cross_links`` are ``(top-band node, lower-band node)``: each top-band island slides over its partners.
    """
    top = next(iter(arranged), None)
    arranged = {section: islands for section, islands in arranged.items() if islands}
    chosen = _targets(arranged, gap, band_gap)
    positions: _Positions = {}
    shelves: dict[str, list[_Shelf]] = defaultdict(list)
    band_y, canvas_width = margin, 2 * margin
    for section, islands in arranged.items():
        x, y, shelf_height = margin, band_y, 0.0
        for local, width, height in islands:
            if x > margin and x + width > margin + chosen[section]:
                x, y, shelf_height = margin, y + shelf_height + gap, 0.0
            positions.update({k: (u + x, v + y) for k, (u, v) in local.items()})
            shelves[section].append((y, x, width, tuple(local)))
            canvas_width = max(canvas_width, x + width + margin)
            shelf_height = max(shelf_height, height)
            x += width + gap
        band_y = y + shelf_height + band_gap
    if top in shelves:
        drivers = defaultdict(list)
        for upper, lower in cross_links:
            drivers[upper].append(positions[lower][0])
        _over_their_drivers(positions, shelves[top], drivers, gap=gap, margin=margin, canvas_width=canvas_width)
    height = band_y - band_gap + margin if positions else 2 * margin
    return Packed(positions, canvas_width, height)
