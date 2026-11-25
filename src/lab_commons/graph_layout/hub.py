"""Where a multi-port net meets: a lane point outside every box, wire and blocked port."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from lab_commons.graph_layout.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lab_commons.graph_layout.geometry import Box, Segment

__all__ = ['free_hub']


def _lanes(axis: str, preferred: Point, boxes: Sequence[Box], occupied: Sequence[Segment], step: float) -> set[float]:
    size = 'width' if axis == 'x' else 'height'
    return {
        getattr(preferred, axis),
        *(getattr(b, axis) + sign * (getattr(b, size) / 2.0 + 2 * step) for b in boxes for sign in (-1, 1)),
        *(
            getattr(w.start, axis) + offset
            for w in occupied
            if getattr(w.start, axis) == getattr(w.end, axis)
            for offset in (-step, step)
        ),
    }


def _outside(boxes: Sequence[Box], occupied: Sequence[Segment], blocked: set[Point]) -> Callable[[Point], bool]:
    vertical, horizontal = defaultdict(list), defaultdict(list)
    for w in occupied:
        if w.start.x == w.end.x:
            vertical[w.start.x].append(tuple(sorted((w.start.y, w.end.y))))
        elif w.start.y == w.end.y:
            horizontal[w.start.y].append(tuple(sorted((w.start.x, w.end.x))))

    def outside(p: Point) -> bool:
        return (
            not any(b.inside(p) for b in boxes)
            and all(not low <= p.y <= high for low, high in vertical[p.x])
            and all(not low <= p.x <= high for low, high in horizontal[p.y])
            and p not in blocked
        )

    return outside


def free_hub(
    points: Sequence[Point],
    preferred: Point,
    boxes: Sequence[Box],
    *,
    occupied: Sequence[Segment] = (),
    blocked_points: Sequence[Point] = (),
    preserve_preferred: bool = False,
    lane_step: float = 6.0,
) -> Point:
    """A net's hub on a lane point outside every box, wire and blocked port, near ``preferred``.

    Least total Manhattan distance to ``points`` wins, then least displacement (swapped when
    ``preserve_preferred``); y then x break ties. ``lane_step`` spaces the candidate lanes as in
    :func:`route.route_orthogonal`. Refused by name when every lane point is taken.
    """
    xs, ys = (_lanes(axis, preferred, boxes, occupied, lane_step) for axis in ('x', 'y'))
    outside = _outside(boxes, occupied, set(blocked_points))
    total_x = {x: sum(abs(x - p.x) for p in points) for x in xs}
    total_y = {y: sum(abs(y - p.y) for p in points) for y in ys}

    def cost(p: Point) -> tuple[float, float, float, float]:
        total = total_x[p.x] + total_y[p.y]
        displacement = abs(p.x - preferred.x) + abs(p.y - preferred.y)
        primary, secondary = (displacement, total) if preserve_preferred else (total, displacement)
        return primary, secondary, p.y, p.x

    best = min((p for p in (Point(x, y) for x in xs for y in ys) if outside(p)), key=cost, default=None)
    if best is None:
        msg = 'free_hub: every lane point lies inside a box, on a wire or on a blocked port; widen lane_step'
        raise ValueError(msg)
    return best
