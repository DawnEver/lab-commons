"""Domain-free graph layout and orthogonal routing: boxes, nets, layered placement, routed wires, SVG out.

Coordinates are unitless numbers in ONE caller-declared drawing unit; nothing here converts them. What a
node IS (a component, a block, a pin's meaning, a vendor format) is the caller's adapter. Start with
:func:`placement.place_graph` (where each box goes), then :func:`route.route_orthogonal` /
:func:`nets.route_net` (the wires), then :func:`svg.layout_svg` (a picture).
"""

from lab_commons.graph_layout.anchors import stack_beside, stub_center
from lab_commons.graph_layout.clearance import RoutingClearance
from lab_commons.graph_layout.geometry import (
    Box,
    Point,
    Segment,
    box_boundary,
    crosses,
    nearest_tree_point,
    path_segments,
    wire_contact,
)
from lab_commons.graph_layout.hub import free_hub
from lab_commons.graph_layout.nets import (
    CrossingBoundReached,
    CrossingTally,
    fewest_crossings,
    route_in_fewest_crossings_order,
    route_net,
    shortest_span_first,
)
from lab_commons.graph_layout.placement import Net, Node, Placement, place_graph
from lab_commons.graph_layout.route import CROSSING_LANES, Unroutable, route_orthogonal
from lab_commons.graph_layout.search import search_orthogonal
from lab_commons.graph_layout.svg import layout_svg

__all__ = [
    'CROSSING_LANES',
    'Box',
    'CrossingBoundReached',
    'CrossingTally',
    'Net',
    'Node',
    'Placement',
    'Point',
    'RoutingClearance',
    'Segment',
    'Unroutable',
    'box_boundary',
    'crosses',
    'fewest_crossings',
    'free_hub',
    'layout_svg',
    'nearest_tree_point',
    'path_segments',
    'place_graph',
    'route_in_fewest_crossings_order',
    'route_net',
    'route_orthogonal',
    'search_orthogonal',
    'shortest_span_first',
    'stack_beside',
    'stub_center',
    'wire_contact',
]
