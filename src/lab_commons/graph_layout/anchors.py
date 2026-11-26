"""Boxes that hang off one port rather than join the layered graph: a stub, or a stack beside a face."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ['stack_beside', 'stub_center']

_XY = tuple[float, float]


def stub_center(pin: _XY, direction: tuple[int, int], size: _XY, *, stub: float) -> _XY:
    """Center of a box of ``size`` drawn as a short stub off ``pin``, outward along the pin's ``direction``.

    A box serving exactly one port (a connector label) goes there -- never stacked as a graph node in its band.
    """
    (x, y), (dx, dy), (width, height) = pin, direction, size
    reach = stub + (width if dx else height) / 2
    return x + dx * reach, y + dy * reach


def stack_beside(
    anchored: Iterable[tuple[str, _XY, tuple[int, int], _XY]], *, clearance: float, gap: float
) -> dict[str, _XY]:
    """Islands placed beside the ports they hang off: ``(key, port, direction, size)`` -> center.

    Each sits ``clearance`` off its port along the port's outward direction. Islands sharing a face keep
    their ports' order across it, stay ``gap`` apart, and the stack centers on its ports.
    """
    faces = defaultdict(list)
    for key, port, direction, size in anchored:
        faces[direction, port[0] if direction[0] else port[1]].append((key, port, direction, size))
    placed = {}
    for ((dx, dy), _), members in faces.items():
        along = 1 if dx else 0  # the axis the stack runs along: y on an east/west face, x on a north/south one
        members.sort(key=lambda m: (m[1][along], m[0]))
        cursor, raw = None, []
        for _, port, _, size in members:
            want = port[along]
            if cursor is not None:
                want = max(want, cursor + gap + size[along] / 2)
            raw.append(want)
            cursor = want + size[along] / 2
        shift = (sum(m[1][along] for m in members) - sum(raw)) / len(members)
        for (key, port, _, size), coordinate in zip(members, raw, strict=True):
            out = port[1 - along] + (dx or dy) * (clearance + size[1 - along] / 2)
            placed[key] = (out, coordinate + shift) if along else (coordinate + shift, out)
    return placed
