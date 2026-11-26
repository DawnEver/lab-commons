"""Indexed clearance: open box and wire interiors, closed foreign ports, strict-interior foreign crossings."""

import pytest

from lab_commons.graph_layout import Box, Point, RoutingClearance, Segment, box_boundary, wire_contact


def _seg(a: tuple[float, float], b: tuple[float, float]) -> Segment:
    return Segment(Point(*a), Point(*b))


@pytest.mark.parametrize(
    ('start', 'end', 'expected'),
    [
        ((0, 5), (3, 5), True),
        ((0, 5), (3.1, 5), False),
        ((0, 3), (10, 3), True),
        ((0, 7), (10, 7), True),
        ((5, 0), (5, 3), True),
        ((5, 0), (5, 3.1), False),
        ((3, 0), (3, 10), True),
        ((7, 0), (7, 10), True),
        ((7, 5), (10, 5), True),
        ((6.9, 5), (10, 5), False),
    ],
)
def test_box_interiors_are_open(start, end, expected) -> None:
    assert RoutingClearance((Box('n', 5, 5, 4, 4),)).clear((_seg(start, end),)) is expected


def test_collinear_overlap_is_refused_but_a_perpendicular_crossing_of_an_occupied_wire_is_not() -> None:
    index = RoutingClearance((), occupied=(_seg((3, 10), (7, 10)),))
    assert index.clear((_seg((0, 10), (3, 10)),))
    assert not index.clear((_seg((0, 10), (3.1, 10)),))
    assert index.clear((_seg((5, 8), (5, 12)),))


def test_a_foreign_port_is_a_closed_point_even_at_an_endpoint() -> None:
    index = RoutingClearance((), blocked_points=(Point(2, 2),))
    assert not index.clear((_seg((0, 2), (2, 2)),))
    assert not index.clear((_seg((2, 0), (2, 2)),))
    assert index.clear((_seg((0, 2), (1.9, 2)),))
    assert index.clear((_seg((0, 1.9), (4, 1.9)),))


def test_a_diagonal_candidate_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match='orthogonal segments'):
        RoutingClearance(()).clear((_seg((0, 0), (1, 1)),))


def test_repeated_candidates_compile_a_lane_only_once(monkeypatch) -> None:
    index = RoutingClearance(tuple(Box(str(i), i * 10, 20, 4, 4) for i in range(100)))
    calls = []
    original = RoutingClearance._compile
    monkeypatch.setattr(RoutingClearance, '_compile', lambda self, *a: calls.append(a) or original(self, *a))
    for i in range(1000):
        assert index.clear((_seg((0, 1), (i + 1, 1)),))
    assert len(calls) == 1


_FOREIGN = (_seg((0, 5), (10, 5)),)


@pytest.mark.parametrize(
    ('candidate', 'expected'),
    [
        (((5, 0), (5, 10)), True),  # strict interior crossing
        (((5, 0), (5, 5)), False),  # our end on the foreign interior
        (((5, 5), (5, 10)), False),
        (((0, 0), (0, 10)), False),  # the foreign end on our interior
        (((10, 5), (10, 10)), False),  # shared endpoint
        (((10, 5), (20, 5)), False),  # collinear end-to-end
    ],
)
def test_only_a_strict_interior_crossing_may_touch_a_foreign_wire(candidate, expected) -> None:
    assert RoutingClearance((), foreign_wires=_FOREIGN).clear((_seg(*candidate),)) is expected


def test_an_occupied_wire_is_only_never_overlapped() -> None:
    assert RoutingClearance((), _FOREIGN).clear((_seg((10, 5), (10, 10)),))
    assert not RoutingClearance((), _FOREIGN).clear((_seg((5, 5), (20, 5)),))


def test_wire_contact_is_perpendicular_or_a_shared_end_never_a_collinear_overlap() -> None:
    horizontal, vertical = _seg((-20, 1), (20, 1)), _seg((0, -19), (0, 21))
    assert wire_contact(horizontal, vertical) == wire_contact(vertical, horizontal) == (0, 1)
    assert wire_contact(horizontal, _seg((20, 1), (30, 1))) == (20, 1)
    assert wire_contact(horizontal, _seg((-10, 1), (10, 1))) is None


def test_a_box_boundary_is_where_the_center_ray_leaves_it() -> None:
    box = Box('n', 0, 0, 20, 10)
    assert box_boundary(box, Point(100, 0)) == Point(10, 0)
    assert box_boundary(box, Point(0, -100)) == Point(0, -5)
    assert box_boundary(box, Point(0, 0)) == Point(0, 0)
