"""Orthogonal routing of one wire around node boxes: fast lane templates, then a complete lane search.

Every coordinate is in the caller's drawing unit; ``lane_step`` is in that unit and spaces the candidate
lanes, never the clearance rule (which is :class:`RoutingClearance`'s alone).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from itertools import pairwise
from typing import TYPE_CHECKING

from lab_commons.graph_layout.clearance import RoutingClearance
from lab_commons.graph_layout.geometry import Box, Point, Segment, path_segments
from lab_commons.graph_layout.search import search_orthogonal

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ['CROSSING_LANES', 'Unroutable', 'route_orthogonal']


class Unroutable(ValueError):
    """No obstacle-free orthogonal route exists on the lane graph between two named points.

    The door: move a box, free a blocked port, or let the wire cross (pass it as ``foreign_wires`` rather
    than ``occupied``). A ``ValueError`` so net-level drawers (:func:`nets.route_net`) can fall back.
    """

    def __init__(self, start: Point, end: Point) -> None:
        """Name both endpoints and what blocks a lane."""
        self.start, self.end = start, end
        super().__init__(
            f'Unroutable: no obstacle-free orthogonal route from ({start.x}, {start.y}) to ({end.x}, {end.y}); '
            'every lane is blocked by a box interior, an occupied wire or a foreign port'
        )


#: What one wire crossing costs a route, in LANE STEPS (so in the caller's drawing unit via ``lane_step``).
CROSSING_LANES = 6.0
#: How many lanes nearest each endpoint the five-segment templates try.
_NEAR_LANES = 8

_Path = tuple[Point, ...]


def _lanes(
    axis: str,
    ends: tuple[Point, Point],
    boxes: Sequence[Box],
    wires: Sequence[Segment],
    *,
    foreign: Sequence[Segment],
    blocked: Sequence[Point],
    step: float,
) -> list[float]:
    """Candidate lane coordinates along ``axis`` ('x' or 'y'): off every box, wire, port and foreign end."""
    size = 'width' if axis == 'x' else 'height'
    return sorted(
        {
            min(getattr(p, axis) for p in ends) - 2 * step,
            *(getattr(b, axis) + sign * (getattr(b, size) / 2.0 + 2 * step) for b in boxes for sign in (-1, 1)),
            *(
                getattr(w.start, axis) + offset
                for w in wires
                if getattr(w.start, axis) == getattr(w.end, axis)
                for offset in (-step, step)
            ),
            *(getattr(p, axis) + offset for p in blocked for offset in (-step, step)),
            # A lane just past each end of another net's wire, so a route can go AROUND it instead of across.
            *(getattr(end, axis) + offset for w in foreign for end in (w.start, w.end) for offset in (-step, step)),
        }
    )


class _Crossings:
    """Strict interior crossings of a candidate with existing wires, by bisection on their fixed coordinate."""

    def __init__(self, wires: Sequence[Segment]) -> None:
        """Index ``wires`` once: (x, y-span) verticals, (y, x-span) horizontals."""
        verticals = [w for w in wires if w.start.x == w.end.x and w.start.y != w.end.y]
        horizontals = [w for w in wires if w.start.y == w.end.y and w.start.x != w.end.x]
        self._walls = (
            sorted((w.start.x, *sorted((w.start.y, w.end.y))) for w in verticals),
            sorted((w.start.y, *sorted((w.start.x, w.end.x))) for w in horizontals),
        )
        self._at = tuple([w[0] for w in walls] for walls in self._walls)

    def count(self, segments: Sequence[Segment]) -> int:
        """The crossings ``segments`` make with the indexed wires."""
        total = 0
        for s in segments:
            a, b = s.start, s.end
            if a.y == b.y and a.x != b.x:
                (low, high), lane, fixed = sorted((a.x, b.x)), 0, a.y
            elif a.x == b.x and a.y != b.y:
                (low, high), lane, fixed = sorted((a.y, b.y)), 1, a.x
            else:
                continue
            walls, at = self._walls[lane], self._at[lane]
            total += sum(walls[i][1] < fixed < walls[i][2] for i in range(bisect_right(at, low), bisect_left(at, high)))
        return total


def _families(point: Point, hub: Point, xs: list[float], ys: list[float]) -> Iterator[list[_Path]]:
    """Template families, fewest bends first: L and Z shapes, then three-lane, then two five-segment shapes."""
    yield [
        (point, Point(hub.x, point.y), hub),
        (point, Point(point.x, hub.y), hub),
        *((point, Point(point.x, y), Point(hub.x, y), hub) for y in ys),
        *((point, Point(x, point.y), Point(x, hub.y), hub) for x in xs),
    ]
    yield [(point, Point(x, point.y), Point(x, y), Point(hub.x, y), hub) for x in xs for y in ys]
    near_start_x = sorted(xs, key=lambda x: (abs(x - point.x), x))[:_NEAR_LANES]
    near_end_x = sorted(xs, key=lambda x: (abs(x - hub.x), x))[:_NEAR_LANES]
    yield [
        (point, Point(x1, point.y), Point(x1, y), Point(x2, y), Point(x2, hub.y), hub)
        for x1 in near_start_x
        for x2 in near_end_x
        for y in ys
    ]
    near_start_y = sorted(ys, key=lambda y: (abs(y - point.y), y))[:_NEAR_LANES]
    near_end_y = sorted(ys, key=lambda y: (abs(y - hub.y), y))[:_NEAR_LANES]
    yield [
        (point, Point(point.x, y1), Point(x, y1), Point(x, y2), Point(hub.x, y2), hub)
        for y1 in near_start_y
        for y2 in near_end_y
        for x in xs
    ]


def _cost(points: _Path, *, prefer_vertical: bool) -> tuple[float, int, int, tuple[tuple[float, float], ...]]:
    """Length, segments, vertical-first penalty, vertices -- read off the points, no segment built."""
    pairs = [(a, b) for a, b in pairwise(points) if a != b]
    length = sum(abs(b.x - a.x) + abs(b.y - a.y) for a, b in pairs)
    vertical_penalty = int(prefer_vertical and pairs[0][0].x != pairs[0][1].x)
    return length, len(pairs), vertical_penalty, tuple((b.x, b.y) for _a, b in pairs)


def _best_clear(
    candidates: list[_Path],
    clearance: RoutingClearance,
    crossings: _Crossings,
    *,
    crossing_length: float,
    prefer_vertical: bool,
) -> tuple[Segment, ...] | None:
    """The clear candidate of least length plus ``crossing_length`` per wire it crosses."""
    # Length alone orders the bands (cheap); the full key is built only inside a band that is examined.
    bands = defaultdict(list)
    for index, c in enumerate(candidates):
        bands[sum(abs(b.x - a.x) + abs(b.y - a.y) for a, b in pairwise(c))].append(index)
    clear = None
    for length in sorted(bands):
        if clear is not None and length > clear[0][0]:
            break  # length is a lower bound of every later band's weighted cost
        for key, index in sorted((_cost(candidates[i], prefer_vertical=prefer_vertical), i) for i in bands[length]):
            segments = path_segments(candidates[index])
            if clearance.clear(segments):
                count = crossings.count(segments)
                ranked = (key[0] + crossing_length * count, count, *key[1:])
                if clear is None or ranked < clear[0]:
                    clear = (ranked, segments)
    return None if clear is None else clear[1]


def _check_step(lane_step: float) -> None:
    if (
        isinstance(lane_step, bool)
        or not isinstance(lane_step, (int, float))
        or not math.isfinite(lane_step)
        or lane_step <= 0
    ):
        msg = 'routing lane step must be finite and positive'
        raise ValueError(msg)


def route_orthogonal(
    point: Point,
    hub: Point,
    boxes: Sequence[Box],
    *,
    occupied: Sequence[Segment] = (),
    foreign_wires: Sequence[Segment] = (),
    blocked_points: Sequence[Point] = (),
    prefer_vertical: bool = False,
    lane_step: float = 6.0,
) -> tuple[Segment, ...]:
    """The orthogonal wire from ``point`` to ``hub`` around ``boxes``; :class:`Unroutable` when none exists.

    ``occupied`` wires are only never overlapped (a directed link drawn over a net). ``foreign_wires`` are
    OTHER NETS: the route may cross one only at a strict interior point (:class:`RoutingClearance`), and each
    crossing costs ``CROSSING_LANES`` lane steps of length. Fast templates are tried first, fewest bends first;
    only when every template is blocked does the complete lane search run.
    """
    _check_step(lane_step)
    if point == hub:
        return ()
    boxes = tuple(boxes)
    clearance = RoutingClearance(boxes, tuple(occupied), tuple(blocked_points), tuple(foreign_wires))
    wires = (*occupied, *foreign_wires)
    lanes = {
        axis: _lanes(axis, (point, hub), boxes, wires, foreign=foreign_wires, blocked=blocked_points, step=lane_step)
        for axis in ('x', 'y')
    }
    crossings, crossing_length = _Crossings(wires), CROSSING_LANES * lane_step
    for family in _families(point, hub, lanes['x'], lanes['y']):
        if wire := _best_clear(
            family, clearance, crossings, crossing_length=crossing_length, prefer_vertical=prefer_vertical
        ):
            return wire
    # Box boundaries also expose narrow corridors the templates' two-lane offsets can miss.
    edges = {
        axis: [getattr(b, axis) + sign * getattr(b, size) / 2 for b in boxes for sign in (-1, 1)]
        for axis, size in (('x', 'width'), ('y', 'height'))
    }
    if wire := search_orthogonal(
        point,
        hub,
        clearance,
        xs=(*lanes['x'], *edges['x']),
        ys=(*lanes['y'], *edges['y']),
        prefer_vertical=prefer_vertical,
    ):
        return wire
    raise Unroutable(point, hub)
