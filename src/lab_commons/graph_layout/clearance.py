"""Route-local interval indexes: the ONE clearance authority every route and search consults."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict

from lab_commons.graph_layout.geometry import Box, Point, Segment

__all__ = ['RoutingClearance']


class RoutingClearance:
    """Compile each visited X/Y lane once, not once per candidate segment.

    Box interiors and ``occupied`` collinear overlaps are OPEN intervals. ``blocked_points`` (foreign ports)
    are CLOSED points, including candidate endpoints. A ``foreign_wires`` wire (another net) may be touched
    ONLY by a strict-interior perpendicular crossing: its ends are closed points, and a candidate's end or
    bend on it is refused -- every other contact would join the two nets.
    """

    def __init__(
        self,
        boxes: tuple[Box, ...],
        occupied: tuple[Segment, ...] = (),
        blocked_points: tuple[Point, ...] = (),
        foreign_wires: tuple[Segment, ...] = (),
    ) -> None:
        """Index ``boxes``, wires and ports; lanes compile lazily on first use."""
        self._boxes = tuple(
            (s.x - s.width / 2, s.x + s.width / 2, s.y - s.height / 2, s.y + s.height / 2) for s in boxes
        )
        self._occupied = (defaultdict(list), defaultdict(list))
        self._pins = (defaultdict(set), defaultdict(set))
        self._cache = {}
        self._foreign = (defaultdict(list), defaultdict(list))
        for segment in foreign_wires:
            a, b = segment.start, segment.end
            blocked_points = (*blocked_points, a, b)
            if a.y == b.y:
                self._foreign[0][a.y].append(tuple(sorted((a.x, b.x))))
            if a.x == b.x:
                self._foreign[1][a.x].append(tuple(sorted((a.y, b.y))))
        for segment in (*occupied, *foreign_wires):
            a, b = segment.start, segment.end
            if a == b:
                continue
            if a.y == b.y:
                self._occupied[0][a.y].append(tuple(sorted((a.x, b.x))))
            elif a.x == b.x:
                self._occupied[1][a.x].append(tuple(sorted((a.y, b.y))))
        for point in blocked_points:
            self._pins[0][point.y].add(point.x)
            self._pins[1][point.x].add(point.y)

    def _compile(self, axis: int, coordinate: float) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
        intervals = list(self._occupied[axis].get(coordinate, ()))
        for left, right, top, bottom in self._boxes:
            if axis == 0 and top < coordinate < bottom:
                intervals.append((left, right))
            elif axis == 1 and left < coordinate < right:
                intervals.append((top, bottom))
        merged = []
        for low, high in sorted(intervals):
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
            else:
                merged.append((low, high))
        return (
            tuple(low for low, _high in merged),
            tuple(high for _low, high in merged),
            tuple(sorted(self._pins[axis].get(coordinate, ()))),
        )

    def clear(self, segments: tuple[Segment, ...]) -> bool:
        """Whether every segment keeps clear (see the class docstring); a diagonal one is refused by name."""
        for segment in segments:
            a, b = segment.start, segment.end
            if a == b:
                continue
            if a.y == b.y:
                axis, coordinate, bounds = 0, a.y, (a.x, b.x)
            elif a.x == b.x:
                axis, coordinate, bounds = 1, a.x, (a.y, b.y)
            else:
                msg = 'routing clearance requires orthogonal segments'
                raise ValueError(msg)
            key = axis, coordinate
            if key not in self._cache:
                self._cache[key] = self._compile(*key)
            starts, ends, pins = self._cache[key]
            low, high = sorted(bounds)
            interval = bisect_left(starts, high) - 1
            if interval >= 0 and ends[interval] > low:
                return False
            point = bisect_left(pins, low)
            if point < len(pins) and pins[point] <= high:
                return False
            if self._on_wire(a) or self._on_wire(b):
                return False
        return True

    def _on_wire(self, point: Point) -> bool:
        """A candidate end or bend lying on a foreign wire (closed): a joining contact."""
        return any(
            low <= value <= high
            for lane, coordinate, value in ((0, point.y, point.x), (1, point.x, point.y))
            for low, high in self._foreign[lane].get(coordinate, ())
        )
