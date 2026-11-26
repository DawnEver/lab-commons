"""Finite orthogonal lane search, for when the fast routing templates are exhausted."""

from __future__ import annotations

from heapq import heappop, heappush
from itertools import pairwise
from typing import TYPE_CHECKING

from lab_commons.graph_layout.geometry import Point, Segment

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lab_commons.graph_layout.clearance import RoutingClearance

__all__ = ['search_orthogonal']

#: A search state: lane indexes (x, y) and the axis it arrived along (0 none, 1 horizontal, 2 vertical).
_State = tuple[int, int, int]
_MIN_RUN = 2


def _simplified(vertices: list[Point]) -> tuple[Segment, ...]:
    """The path through ``vertices`` with every collinear interior vertex dropped."""
    kept: list[Point] = []
    for point in vertices:
        if len(kept) >= _MIN_RUN:
            a, b = kept[-2:]
            if a.x == b.x == point.x or a.y == b.y == point.y:
                kept.pop()
        kept.append(point)
    return tuple(Segment(a, b) for a, b in pairwise(kept))


def _walk_back(state: _State, initial: _State, parents: dict, xs: list[float], ys: list[float]) -> list[Point]:
    vertices = []
    while True:
        vertices.append(Point(xs[state[0]], ys[state[1]]))
        if state == initial:
            return vertices[::-1]
        state = parents[state]


def search_orthogonal(
    start: Point,
    end: Point,
    clearance: RoutingClearance,
    *,
    xs: Iterable[float],
    ys: Iterable[float],
    prefer_vertical: bool = False,
) -> tuple[Segment, ...] | None:
    """A* on adjacent lane intersections, with no fixed bend-count ceiling; ``None`` when no path exists.

    Complete on this finite lane graph, NOT a claim about every continuous free-space path. Length, bends,
    then initial direction order the search. The same clearance authority checks every edge, endpoints too.
    """
    xs, ys = sorted(set(xs) | {start.x, end.x}), sorted(set(ys) | {start.y, end.y})
    initial: _State = (xs.index(start.x), ys.index(start.y), 0)
    target = (xs.index(end.x), ys.index(end.y))
    best, parents = {initial: (0.0, 0, 0)}, {}

    def estimate(x: int, y: int) -> float:
        return abs(xs[x] - end.x) + abs(ys[y] - end.y)

    queue = [(estimate(*initial[:2]), 0, 0, 0.0, *initial)]
    while queue:
        _, bends, penalty, length, x, y, axis = heappop(queue)
        state = x, y, axis
        if best.get(state) != (length, bends, penalty):
            continue
        if (x, y) == target:
            return _simplified(_walk_back(state, initial, parents, xs, ys))
        for u, v, direction in ((x - 1, y, 1), (x + 1, y, 1), (x, y - 1, 2), (x, y + 1, 2)):
            if not (0 <= u < len(xs) and 0 <= v < len(ys)):
                continue
            a, b = Point(xs[x], ys[y]), Point(xs[u], ys[v])
            if not clearance.clear((Segment(a, b),)):
                continue
            distance = length + abs(a.x - b.x) + abs(a.y - b.y)
            turns = bends + int(axis not in {0, direction})
            first = int(prefer_vertical and direction == 1) if axis == 0 else penalty
            cost, neighbour = (distance, turns, first), (u, v, direction)
            if neighbour not in best or cost < best[neighbour]:
                best[neighbour], parents[neighbour] = cost, state
                heappush(queue, (distance + estimate(u, v), turns, first, distance, *neighbour))
    return None
