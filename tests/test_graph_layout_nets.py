"""Whole-net rules: a net is a bundle not a star, nets route shortest first, the drawing crossing least wins."""

from collections.abc import Callable
from typing import Never

import pytest

from lab_commons.graph_layout import (
    CrossingBoundReached,
    CrossingTally,
    Point,
    Segment,
    nearest_tree_point,
    route_in_fewest_crossings_order,
    route_net,
    shortest_span_first,
)


def _p(x: float, y: float) -> Point:
    return Point(x, y)


def test_nets_route_shortest_span_first_with_the_key_breaking_ties() -> None:
    nets = [('long', [_p(0, 0), _p(100, 0)]), ('b', [_p(0, 0), _p(5, 5)]), ('a', [_p(9, 9), _p(14, 14)])]
    assert [key for key, _points in shortest_span_first(nets)] == ['a', 'b', 'long']


def test_a_branch_joins_the_nearest_point_of_its_net_tree_not_the_hub() -> None:
    tree = (Segment(_p(0, 0), _p(100, 0)), Segment(_p(100, 0), _p(100, 50)))
    assert nearest_tree_point(_p(40, 30), tree) == _p(40, 0)
    assert nearest_tree_point(_p(130, 45), tree) == _p(100, 45)
    assert nearest_tree_point(_p(-5, -5), ()) is None


def test_one_net_rule_joins_nearest_first_and_falls_back_to_the_hub() -> None:
    hub, near, far = _p(0, 0), _p(10, 10), _p(50, 10)

    def run(refused: Callable[[Point], bool] = lambda _: False, unroutable: tuple = ()) -> list:
        calls = []

        def route(start: Point, target: Point) -> tuple[Segment, Segment]:
            calls.append((start, target))
            if target in unroutable:
                msg = 'blocked'
                raise ValueError(msg)
            return (Segment(start, _p(start.x, target.y)), Segment(_p(start.x, target.y), target))

        route_net((far, near), hub, route, refused=refused)
        return calls

    assert run() == [(near, hub), (far, near)]
    assert run(refused=lambda p: p == near) == [(near, hub), (far, hub)]
    assert run(unroutable=(near,)) == [(near, hub), (far, near), (far, hub)]


_NETS = [('a', [_p(0, 0), _p(100, 0)]), ('b', [_p(0, 0), _p(1, 1)])]  # declared a, b; span order b, a
_CROSSED = {'a': (Segment(_p(0, 5), _p(10, 5)),), 'b': (Segment(_p(5, 0), _p(5, 10)),)}
_APART = {'a': (Segment(_p(0, 5), _p(10, 5)),), 'b': (Segment(_p(20, 0), _p(20, 10)),)}


@pytest.mark.parametrize(('declared', 'span', 'expected'), [(_CROSSED, _APART, _APART), (_APART, _CROSSED, _APART)])
def test_the_order_whose_drawing_crosses_less_wins(declared, span, expected) -> None:
    drawn = {('a', 'b'): declared, ('b', 'a'): span}
    assert route_in_fewest_crossings_order(_NETS, lambda order, _: drawn[tuple(order)]) == expected


def test_a_tie_keeps_the_declared_order_and_an_unroutable_order_loses() -> None:
    tie = {('a', 'b'): dict(_APART, tag=()), ('b', 'a'): dict(_APART)}
    assert 'tag' in route_in_fewest_crossings_order(_NETS, lambda order, _: tie[tuple(order)])

    def declared_fails(order: tuple, _: object) -> dict:
        if tuple(order) == ('a', 'b'):
            msg = 'no route'
            raise ValueError(msg)
        return _CROSSED

    assert route_in_fewest_crossings_order(_NETS, declared_fails) == _CROSSED


def test_when_every_order_fails_the_failure_names_its_cause() -> None:
    def never(_order, _tally) -> Never:
        msg = 'Unroutable: planted'
        raise ValueError(msg)

    with pytest.raises(ValueError, match='Unroutable: planted'):
        route_in_fewest_crossings_order(_NETS, never)


def test_a_tally_stops_a_drawing_once_it_reaches_the_kept_count() -> None:
    tally = CrossingTally(bound=1)
    tally.add('a', _CROSSED['a'])
    with pytest.raises(CrossingBoundReached):
        tally.add('b', _CROSSED['b'])
    CrossingTally(bound=None).add('b', _CROSSED['b'])


def test_a_later_order_that_reaches_the_bound_is_abandoned_and_the_first_kept() -> None:
    seen = []

    def route_all(order: tuple, tally: CrossingTally) -> dict:
        seen.append((tuple(order), tally.bound))
        for key in order:
            tally.add(key, _CROSSED[key])
        return _CROSSED

    assert route_in_fewest_crossings_order(_NETS, route_all) == _CROSSED
    assert seen == [(('a', 'b'), None), (('b', 'a'), 1)]


def test_orders_all_abandoned_under_an_outer_bound_abandon_the_outer_drawing() -> None:
    def route_all(order: tuple, tally: CrossingTally) -> dict:
        for key in order:
            tally.add(key, _CROSSED[key])
        return _CROSSED

    with pytest.raises(CrossingBoundReached):
        route_in_fewest_crossings_order(_NETS, route_all, bound=1)
