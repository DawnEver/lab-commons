"""Topology-only layered placement: where each node box goes, from its nets and directed links.

Two bands: ``physical`` nodes (joined by undirected nets, ranked outward from ``source`` nodes) above
``causal`` nodes (joined by directed links, ranked along the links; back edges become ``feedback_edges``,
never removed links). Disconnected islands are shelf-packed. Coordinates are box CENTERS in the caller's
drawing unit; ports, port geometry and what a node means stay with the caller's adapter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lab_commons.graph_layout._motif import Motif, leg_motifs
from lab_commons.graph_layout._ordering import arrange_island, causal_ranks, connected_islands, physical_ranks
from lab_commons.graph_layout._shelves import MAX_ASPECT, pack_bands

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = ['KINDS', 'MAX_ASPECT', 'SECTIONS', 'Net', 'Node', 'Placement', 'place_graph']

#: The two bands, drawn top to bottom.
SECTIONS = ('physical', 'causal')
#: Node roles placement reads: ``leg`` joins the leg-pair motif (:mod:`._motif`), ``reference`` hangs below its source.
KINDS = ('generic', 'leg', 'reference')

_Link = tuple[str, str]


@dataclass(frozen=True)
class Node:
    """One box to place: unique ``key``, envelope ``width`` x ``height``, its band and placement role."""

    key: str
    width: float
    height: float
    section: str = 'physical'
    source: bool = False
    kind: str = 'generic'
    label: str = ''


@dataclass(frozen=True)
class Net:
    """An undirected hyperedge over node keys; a ``reference`` net (a common return) never ranks."""

    key: str
    members: tuple[str, ...]
    reference: bool = False
    physical: bool = True


@dataclass(frozen=True)
class Placement:
    """Box centers, column ranks, the canvas, and the links set aside as feedback."""

    positions: dict[str, tuple[float, float]]
    ranks: dict[str, int]
    width: float
    height: float
    feedback_edges: tuple[_Link, ...]


def _refuse(message: str) -> None:
    raise ValueError(message)


def _validate(
    nodes: Sequence[Node],
    by_key: Mapping[str, Node],
    nets: Sequence[Net],
    links: Sequence[_Link],
    clearances: Iterable[float],
) -> None:
    if len(by_key) != len(nodes) or any(not node.key for node in nodes):
        _refuse('layout requires unique nonempty node keys')
    if any(not math.isfinite(v) or v <= 0 for v in clearances):
        _refuse('layout requires finite positive clearances (gap, margin, band_gap)')
    if any(
        node.section not in SECTIONS
        or node.kind not in KINDS
        or any(not math.isfinite(v) or v <= 0 for v in (node.width, node.height))
        for node in nodes
    ):
        _refuse('layout requires positive finite node boxes, a section in SECTIONS and a kind in KINDS')
    if len({net.key for net in nets}) != len(nets):
        _refuse('layout requires unique net keys')
    if any(k not in by_key for net in nets for k in net.members) or any(
        a not in by_key or b not in by_key for a, b in links
    ):
        _refuse('layout edge names an absent node')


def _adjacency(by_key: Mapping[str, Node], nets: Sequence[Net], links: Sequence[_Link]) -> dict[str, set[str]]:
    """Same-band neighbors through every non-reference net and every link."""
    adjacency = {k: set() for k in by_key}
    for net in nets:
        if net.reference:
            continue
        members = set(net.members)
        for key in members:
            adjacency[key].update(k for k in members if k != key and by_key[k].section == by_key[key].section)
    for a, b in links:
        if by_key[a].section == by_key[b].section:
            adjacency[a].add(b)
            adjacency[b].add(a)
    return adjacency


def _place_motifs(
    nodes: Sequence[Node],
    nets: Sequence[Net],
    links: Sequence[_Link],
    motifs: Mapping[str, Motif],
    *,
    gap: float,
    margin: float,
    band_gap: float,
    refine: bool,
) -> Placement:
    """Pack each motif as ONE envelope, then restore every original identity."""
    owners = {k: source for source, motif in motifs.items() for k in motif.positions}
    collapsed = tuple(
        replace(node, width=motifs[node.key].width, height=motifs[node.key].height, kind='generic')
        if node.key in motifs
        else node
        for node in nodes
        if node.key not in owners or owners[node.key] == node.key
    )
    collapsed_nets = tuple(replace(net, members=tuple(sorted({owners.get(k, k) for k in net.members}))) for net in nets)
    collapsed_links = tuple(
        (owners.get(a, a), owners.get(b, b)) for a, b in links if owners.get(a, a) != owners.get(b, b)
    )
    result = place_graph(
        collapsed, collapsed_nets, collapsed_links, gap=gap, margin=margin, band_gap=band_gap, refine=refine
    )
    positions, ranks = dict(result.positions), dict(result.ranks)
    for source, motif in motifs.items():
        x, y = result.positions[source]
        for key, (u, v) in motif.positions.items():
            positions[key] = x - motif.width / 2 + u, y - motif.height / 2 + v
            ranks[key] = result.ranks[source] + int(key != source)
    return replace(result, positions=positions, ranks=ranks)


def place_graph(
    nodes: Iterable[Node],
    nets: Iterable[Net] = (),
    directed_links: Iterable[_Link] = (),
    *,
    gap: float = 32.0,
    margin: float = 32.0,
    band_gap: float = 96.0,
    refine: bool = False,
) -> Placement:
    """Arrange exact identities by physical flow and causal flow in separate bands; deterministic.

    Disconnected islands are shelf-packed by envelope area, never one diagonal column each. Reference nets
    are ignored ONLY for ranking. ``refine`` searches each island's row order harder; a caller draws both and
    keeps the refined placement only when its ROUTED drawing crosses less (:func:`nets.fewest_crossings`).
    ``gap``, ``margin`` and ``band_gap`` are in the drawing unit. A malformed graph is refused by name.
    """
    nodes, nets, links = tuple(nodes), tuple(nets), tuple(sorted(set(directed_links)))
    by_key = {node.key: node for node in nodes}
    _validate(nodes, by_key, nets, links, (gap, margin, band_gap))
    if motifs := leg_motifs(by_key, nets, gap):
        return _place_motifs(nodes, nets, links, motifs, gap=gap, margin=margin, band_gap=band_gap, refine=refine)
    adjacency = _adjacency(by_key, nets, links)
    all_ranks, feedback, arranged = {}, set(), {}
    for section in SECTIONS:
        arranged[section] = []
        for group in connected_islands((k for k in by_key if by_key[k].section == section), adjacency):
            if section == 'physical':
                ranks = physical_ranks(group, adjacency, by_key)
            else:
                ranks, backwards = causal_ranks(group, links, by_key)
                feedback.update(backwards)
            all_ranks.update(ranks)
            arranged[section].append(arrange_island(group, ranks, adjacency, by_key, gap, refine=refine))
    cross = [
        (own, other)
        for a, b in links
        for own, other in ((a, b), (b, a))
        if by_key[own].section == 'physical' and by_key[other].section == 'causal'
    ]
    packed = pack_bands(arranged, cross, gap=gap, margin=margin, band_gap=band_gap)
    return Placement(packed.positions, all_ranks, packed.width, packed.height, tuple(sorted(feedback)))
