"""Whole-net rules on top of one-wire routing: bundle a net, order the nets, keep the drawing that crosses least."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lab_commons.graph_layout.geometry import Point, Segment, crosses, nearest_tree_point

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

__all__ = [
    'CrossingBoundReached',
    'CrossingTally',
    'fewest_crossings',
    'route_in_fewest_crossings_order',
    'route_net',
    'shortest_span_first',
]

#: ``(net key, its port points)``.
_KeyedNet = tuple[str, 'Sequence[Point]']


def shortest_span_first(nets: Iterable[_KeyedNet]) -> list[_KeyedNet]:
    """``(key, points)`` nets in routing order: smallest bounding half-perimeter first, key breaking ties.

    A short net routed early is not fenced in; a long one routed later bends around it.
    """

    def span(net: _KeyedNet) -> tuple[float, str]:
        xs, ys = [p.x for p in net[1]], [p.y for p in net[1]]
        return max(xs) - min(xs) + max(ys) - min(ys), net[0]

    return sorted(nets, key=span)


class CrossingBoundReached(Exception):
    """A candidate drawing already crosses as often as the kept one: it can no longer win, so it stops."""


class CrossingTally:
    """Running crossings of a drawing in progress, net by net; reaching ``bound`` abandons the drawing.

    Lossless: a wire added never removes a crossing, so a partial count at the bound is a final count at least
    as large, and :func:`fewest_crossings` replaces only on STRICTLY fewer.
    """

    def __init__(self, bound: int | None = None) -> None:
        """Start at zero crossings; ``None`` never abandons."""
        self.bound, self.count = bound, 0
        self._lines: list[tuple[str, Segment]] = []

    def add(self, key: str, segments: Iterable[Segment]) -> None:
        """Count ``segments`` (net ``key``) against every other net's wire so far; raise at the bound."""
        segments = tuple(segments)
        self.count += sum(crosses(a, b) for a in segments for other, b in self._lines if other != key)
        self._lines.extend((key, segment) for segment in segments)
        if self.bound is not None and self.count >= self.bound:
            raise CrossingBoundReached


def fewest_crossings[Drawing](
    drawers: Iterable[Callable[[int | None], Drawing]],
    lines: Callable[[Drawing], Iterable[tuple[str, Segment]]],
) -> Drawing:
    """The drawing, of the candidates drawn in PREFERENCE order, crossing fewest wires of different keys.

    A later drawing replaces the kept one only on STRICTLY fewer. ``drawer(bound)`` gets the kept drawing's
    crossings (None for the first) and may stop early by raising :class:`CrossingBoundReached`. The drawing
    decides, never the case: no single order or placement wins everywhere. A drawer that cannot draw
    (``ValueError``) loses to one that can; when none can, the first failure is raised, naming its cause.
    """
    best, failure, abandoned = None, None, False
    for drawer in drawers:
        try:
            drawing = drawer(None if best is None else best[0])
        except CrossingBoundReached:
            abandoned = True
            continue
        except ValueError as error:
            failure = failure or error
            continue
        drawn = list(lines(drawing))
        count = sum(crosses(a, b) for i, (ka, a) in enumerate(drawn) for kb, b in drawn[i + 1 :] if ka != kb)
        if best is None or count < best[0]:
            best = (count, drawing)
    if best is None:
        if abandoned and failure is None:
            raise CrossingBoundReached  # every candidate reached an OUTER bound: this whole drawing loses
        raise failure
    return best[1]


def route_in_fewest_crossings_order(
    nets: Iterable[_KeyedNet],
    route_all: Callable[[tuple[str, ...], CrossingTally], Mapping[str, Any]],
    segments: Callable[[Any], Iterable[Segment]] = lambda value: value,
    bound: int | None = None,
) -> Mapping[str, Any]:
    """Route nets in their DECLARED order, then :func:`shortest_span_first`; keep by :func:`fewest_crossings`.

    ``route_all(keys, tally)`` routes in that order, reports each net's wire to ``tally.add(key, segments)`` and
    returns ``{key: value}``; ``segments(value)`` reads its wire. ``bound`` is an outer drawing's kept count.
    """
    nets = list(nets)
    orders = dict.fromkeys((tuple(k for k, _p in nets), tuple(k for k, _p in shortest_span_first(nets))))

    def drawer(order: tuple[str, ...]) -> Callable[[int | None], Mapping[str, Any]]:
        return lambda inner: route_all(
            order, CrossingTally(min((b for b in (bound, inner) if b is not None), default=None))
        )

    return fewest_crossings(
        [drawer(order) for order in orders],
        lambda routed: ((key, segment) for key, value in routed.items() for segment in segments(value)),
    )


def route_net(
    starts: Iterable[Point],
    hub: Point,
    route: Callable[[Point, Point], Iterable[Segment]],
    *,
    refused: Callable[[Point], bool] = lambda _: False,
) -> tuple[Segment, ...]:
    """THE net rule: a bundle, not a star.

    Starts go nearest-to-hub first; each joins the closest point of the wire already drawn
    (:func:`nearest_tree_point`), else the hub when that point is ``refused`` or ``route`` cannot reach it.
    ``route(start, target)`` returns the branch's segments or raises ``ValueError``.
    """
    tree, segments = [Segment(hub, hub)], []
    for start in sorted(starts, key=lambda p: (abs(p.x - hub.x) + abs(p.y - hub.y), p.x, p.y)):
        join = nearest_tree_point(start, tuple(tree))
        if refused(join):
            join = hub
        for target in dict.fromkeys((join, hub)):
            try:
                branch = tuple(route(start, target))
                break
            except ValueError:
                if target == hub:
                    raise
        tree.extend(branch)
        segments.extend(branch)
    return tuple(segments)
