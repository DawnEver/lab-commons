"""One-wire orthogonal routing: lane units, a complete fallback search, crossing cost, a named refusal.

Planted controls: the checkers this file trusts (``_orthogonal``, ``_enters_a_box``) are first shown to say
NO on a hand-built bad route, so their green on real routes is a measurement.
"""

from collections.abc import Sequence
from itertools import pairwise
from random import Random

import pytest

from lab_commons.graph_layout import (
    CROSSING_LANES,
    Box,
    Point,
    RoutingClearance,
    Segment,
    Unroutable,
    crosses,
    free_hub,
    route_orthogonal,
)
from lab_commons.graph_layout import route as route_module


def _seg(a: tuple[float, float], b: tuple[float, float]) -> Segment:
    return Segment(Point(*a), Point(*b))


def _orthogonal(segments: Sequence[Segment]) -> bool:
    return all(s.start.x == s.end.x or s.start.y == s.end.y for s in segments)


def _enters_a_box(segments, boxes) -> bool:
    """Independent of RoutingClearance: does any orthogonal segment pass through a box's OPEN interior?"""
    for s in segments:
        x0, x1 = sorted((s.start.x, s.end.x))
        y0, y1 = sorted((s.start.y, s.end.y))
        for b in boxes:
            left, right = b.x - b.width / 2, b.x + b.width / 2
            top, bottom = b.y - b.height / 2, b.y + b.height / 2
            if x0 == x1 and left < x0 < right and y0 < bottom and y1 > top:
                return True
            if y0 == y1 and top < y0 < bottom and x0 < right and x1 > left:
                return True
    return False


def _connected(segments: Sequence[Segment], start: Point, end: Point) -> bool:
    return (
        segments[0].start == start and segments[-1].end == end and all(a.end == b.start for a, b in pairwise(segments))
    )


_BOX = Box('n0', 50, 50, 20, 20)


def test_planted_the_checkers_can_say_no() -> None:
    assert not _orthogonal((_seg((0, 0), (10, 10)),))
    assert _enters_a_box((_seg((0, 50), (100, 50)),), (_BOX,))
    assert _enters_a_box((_seg((50, 0), (50, 100)),), (_BOX,))
    assert not _enters_a_box((_seg((0, 40), (100, 40)),), (_BOX,))  # along the boundary is outside
    assert not _connected((_seg((0, 0), (1, 0)), _seg((2, 0), (3, 0))), Point(0, 0), Point(3, 0))


def _scatter(seed: int, count: int = 12) -> tuple[Box, ...]:
    rng = Random(seed)  # noqa: S311 -- a reproducible scatter, not a secret
    boxes = []
    while len(boxes) < count:
        b = Box(f'n{len(boxes)}', rng.randrange(0, 400, 10), rng.randrange(0, 400, 10), 30, 20)
        if all(abs(b.x - o.x) >= 50 or abs(b.y - o.y) >= 40 for o in boxes):
            boxes.append(b)
    return tuple(boxes)


@pytest.mark.parametrize('seed', range(6))
def test_every_route_is_orthogonal_connected_and_never_enters_a_box(seed) -> None:
    boxes = _scatter(seed)
    routed = 0
    for a, b in pairwise(boxes):
        start, end = Point(a.x + a.width / 2, a.y), Point(b.x - b.width / 2, b.y)
        try:
            wire = route_orthogonal(start, end, boxes, lane_step=5)
        except Unroutable:
            continue
        routed += 1
        assert _orthogonal(wire)
        assert _connected(wire, start, end)
        assert not _enters_a_box(wire, boxes)
    assert routed >= len(boxes) // 2  # the property ran on real routes, not on refusals


@pytest.mark.parametrize('seed', range(3))
def test_routing_is_deterministic_and_independent_of_box_order(seed) -> None:
    boxes = _scatter(seed)
    start, end = Point(-20, -20), Point(420, 420)
    first = route_orthogonal(start, end, boxes)
    assert first == route_orthogonal(start, end, boxes)
    assert first == route_orthogonal(start, end, tuple(reversed(boxes)))


def test_an_enclosed_endpoint_is_refused_by_name() -> None:
    walls = (Box('t', 0, -2, 10, 2), Box('b', 0, 2, 10, 2), Box('l', -2, 0, 2, 10), Box('r', 2, 0, 2, 10))
    with pytest.raises(Unroutable, match=r'Unroutable: no obstacle-free orthogonal route from \(0, 0\) to \(20, 20\)'):
        route_orthogonal(Point(0, 0), Point(20, 20), walls)
    assert issubclass(Unroutable, ValueError)  # net drawers fall back on ValueError


def test_the_lane_search_crosses_an_alternating_corridor_without_a_bend_cap() -> None:
    walls = (
        Box('top', 50, -10, 130, 10),
        Box('bottom', 50, 10, 130, 10),
        Box('left', -10, 0, 10, 40),
        Box('right', 110, 0, 10, 40),
        *(Box(str(i), x, -1 if i % 2 == 0 else 1, 4, 10) for i, x in enumerate((20, 40, 60, 80))),
    )
    start, end = Point(0, 0), Point(100, 0)
    wire = route_orthogonal(start, end, walls)
    assert len(wire) > 6
    assert _connected(wire, start, end)
    assert RoutingClearance(walls).clear(wire)
    assert not _enters_a_box(wire, walls)
    assert wire == route_orthogonal(start, end, tuple(reversed(walls)))


def test_lane_step_is_in_caller_units_so_routes_scale_and_translate_with_geometry() -> None:
    def routed(scale: float, dx: float, dy: float) -> tuple:
        box = Box('n', 5 * scale + dx, dy, 2 * scale, 4 * scale)
        wire = route_orthogonal(Point(dx, dy), Point(10 * scale + dx, dy), (box,), lane_step=scale)
        return tuple(
            ((s.start.x - dx) / scale, (s.start.y - dy) / scale, (s.end.x - dx) / scale, (s.end.y - dy) / scale)
            for s in wire
        )

    assert routed(1, 0, 0) == routed(6, 100, -50) == routed(0.5, -200, 80)


@pytest.mark.parametrize('step', [True, 0, -1, float('nan'), float('inf')])
def test_an_invalid_lane_step_is_refused_even_for_a_zero_length_route(step) -> None:
    with pytest.raises(ValueError, match='lane step'):
        route_orthogonal(Point(0, 0), Point(0, 0), (), lane_step=step)


def test_the_router_builds_one_shared_clearance_index(monkeypatch) -> None:
    built = []
    real = route_module.RoutingClearance
    monkeypatch.setattr(route_module, 'RoutingClearance', lambda *a, **k: built.append(1) or real(*a, **k))
    assert route_orthogonal(Point(20, 20), Point(80, 80), ())
    assert len(built) == 1


def test_of_two_equally_short_routes_the_one_crossing_no_wire_wins() -> None:
    wall = _seg((-5, 5), (5, 5))
    wire = route_orthogonal(Point(0, 0), Point(10, 10), (), foreign_wires=(wall,), lane_step=1)
    assert not any(crosses(s, wall) for s in wire)
    assert sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y) for s in wire) == 20


def test_a_crossing_costs_lanes_so_a_short_detour_avoids_it() -> None:
    assert CROSSING_LANES >= 6
    wall = _seg((5, -2), (5, 2))
    wire = route_orthogonal(Point(0, 0), Point(10, 0), (), foreign_wires=(wall,), lane_step=1)
    assert not any(crosses(s, wall) for s in wire)


def test_a_free_hub_leaves_a_box_interior_and_a_boxed_in_hub_is_refused_by_name() -> None:
    hub = free_hub((Point(0, 50), Point(100, 50)), Point(50, 50), (_BOX,))
    assert not _BOX.inside(hub)
    assert hub == free_hub((Point(0, 50), Point(100, 50)), Point(50, 50), (_BOX,))
    with pytest.raises(ValueError, match='free_hub: every lane point'):
        free_hub((Point(0, 0),), Point(0, 0), (), blocked_points=(Point(0, 0),))
