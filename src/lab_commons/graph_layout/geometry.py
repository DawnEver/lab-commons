"""Points, orthogonal segments and node boxes in the caller's drawing unit, and the predicates on them."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ['Box', 'Point', 'Segment', 'box_boundary', 'crosses', 'nearest_tree_point', 'path_segments', 'wire_contact']


@dataclass(frozen=True, order=True)
class Point:
    """A location in the caller's drawing unit."""

    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    """A straight wire piece from ``start`` to ``end``."""

    start: Point
    end: Point

    @property
    def orthogonal(self) -> bool:
        """Whether it runs along one axis."""
        return self.start.x == self.end.x or self.start.y == self.end.y


@dataclass(frozen=True)
class Box:
    """A node's envelope: CENTER ``(x, y)`` and full ``width`` x ``height``; its interior is open."""

    key: str
    x: float
    y: float
    width: float
    height: float

    def inside(self, point: Point) -> bool:
        """Strictly interior: the boundary is where wires attach, never inside."""
        return abs(point.x - self.x) < self.width / 2 and abs(point.y - self.y) < self.height / 2


def path_segments(points: Iterable[Point]) -> tuple[Segment, ...]:
    """The segments joining consecutive points, zero-length ones dropped."""
    return tuple(Segment(start, end) for start, end in pairwise(points) if start != end)


def crosses(a: Segment, b: Segment) -> bool:
    """A strict interior perpendicular crossing of two orthogonal segments."""
    horizontal, vertical = (a, b) if a.start.y == a.end.y else (b, a)
    if horizontal.start.y != horizontal.end.y or vertical.start.x != vertical.end.x:
        return False
    return min(horizontal.start.x, horizontal.end.x) < vertical.start.x < max(
        horizontal.start.x, horizontal.end.x
    ) and min(vertical.start.y, vertical.end.y) < horizontal.start.y < max(vertical.start.y, vertical.end.y)


def wire_contact(left: Segment, right: Segment) -> tuple[float, float] | None:
    """Perpendicular contact or a shared endpoint; NOT a collinear overlap test."""
    horizontal, vertical = (left, right) if left.start.y == left.end.y else (right, left)
    if horizontal.start.y == horizontal.end.y and vertical.start.x == vertical.end.x:
        x0, x1 = sorted((horizontal.start.x, horizontal.end.x))
        y0, y1 = sorted((vertical.start.y, vertical.end.y))
        if x0 <= vertical.start.x <= x1 and y0 <= horizontal.start.y <= y1:
            return vertical.start.x, horizontal.start.y
    shared_endpoints = {left.start, left.end} & {right.start, right.end}
    if shared_endpoints:
        point = min(shared_endpoints, key=lambda item: (item.y, item.x))
        return point.x, point.y
    return None


def box_boundary(box: Box, toward: Point) -> Point:
    """Where the center-to-``toward`` ray leaves ``box``: an attachment point for a port-less link."""
    dx, dy = toward.x - box.x, toward.y - box.y
    if dx == 0.0 and dy == 0.0:
        return Point(box.x, box.y)
    x_scale = float('inf') if dx == 0.0 else box.width / (2.0 * abs(dx))
    y_scale = float('inf') if dy == 0.0 else box.height / (2.0 * abs(dy))
    scale = min(x_scale, y_scale)
    return Point(box.x + dx * scale, box.y + dy * scale)


def nearest_tree_point(point: Point, tree: Iterable[Segment]) -> Point | None:
    """The point of a net's routed tree closest to ``point`` (Manhattan): a branch joins the tree there.

    Routing every port to one hub draws parallel star wires; joining the nearest tree point bundles a net.
    """
    best = None
    for segment in tree:
        x = min(max(point.x, min(segment.start.x, segment.end.x)), max(segment.start.x, segment.end.x))
        y = min(max(point.y, min(segment.start.y, segment.end.y)), max(segment.start.y, segment.end.y))
        key = (abs(point.x - x) + abs(point.y - y), x, y)
        if best is None or key < best:
            best = key
    return None if best is None else Point(best[1], best[2])
