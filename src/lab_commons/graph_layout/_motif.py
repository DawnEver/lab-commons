"""The leg-pair motif: two ``leg`` nodes stacked between a source's two rails, recognized from nets alone.

A ``leg`` node has exactly two physical nets. A midpoint net joins two legs whose other rails share one
source, exactly one rail being a declared reference. Every leg pair of one source packs as ONE envelope:
the source, then the pairs left to right (upper over lower), then the source's ``reference`` nodes below.
Ambiguous or reused legs stay generic. Names and incoming coordinates never decide.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lab_commons.graph_layout.placement import Net, Node

__all__ = ['Motif', 'leg_motifs']

#: A leg has exactly this many physical nets, and a midpoint joins exactly this many legs.
_PAIR = 2

#: ``(upper leg, lower leg, reference nodes on the reference rail)``.
_Leg = tuple[str, str, tuple[str, ...]]


@dataclass(frozen=True)
class Motif:
    """One packed motif: member positions relative to its envelope's top-left, and the envelope."""

    positions: dict[str, tuple[float, float]]
    width: float
    height: float


def _leg_pair(
    midpoint: Net, legs_of: Mapping[str, tuple[Net, ...]], items: Mapping[str, Node]
) -> tuple[str, _Leg] | None:
    """``(source, leg)`` when ``midpoint`` joins one unambiguous leg pair, else ``None``."""
    pair = sorted(k for k in midpoint.members if k in legs_of)
    if midpoint.reference or len(pair) != _PAIR or any(len(legs_of[k]) != _PAIR for k in pair):
        return None
    rails = [next(n for n in legs_of[k] if n is not midpoint) for k in pair]
    if rails[0].reference == rails[1].reference:
        return None
    sources = {k for k in set(rails[0].members) & set(rails[1].members) if items[k].source}
    if len(sources) != 1:
        return None
    upper, lower = pair if rails[1].reference else reversed(pair)
    reference = rails[1] if rails[1].reference else rails[0]
    return sources.pop(), (upper, lower, tuple(k for k in reference.members if items[k].kind == 'reference'))


def _pack(source: str, legs: Sequence[_Leg], refs: Sequence[str], items: Mapping[str, Node], gap: float) -> Motif:
    source_width = max(items[k].width for k in (source, *refs))
    upper_height = max(items[upper].height for upper, _lower, _refs in legs)
    lower_height = max(items[lower].height for _upper, lower, _refs in legs)
    height = max(items[source].height, upper_height + gap + lower_height)
    upper_y = (height - upper_height - gap - lower_height) / 2 + upper_height / 2
    lower_y = upper_y + upper_height / 2 + gap + lower_height / 2
    positions = {source: (source_width / 2, height / 2)}
    x = source_width + gap
    for upper, lower, _ in legs:
        width = max(items[upper].width, items[lower].width)
        positions[upper], positions[lower] = (x + width / 2, upper_y), (x + width / 2, lower_y)
        x += width + gap
    for key in refs:
        positions[key] = source_width / 2, height + gap + items[key].height / 2
        height += gap + items[key].height
    return Motif(positions, x - gap, height)


def leg_motifs(items: Mapping[str, Node], nets: Sequence[Net], gap: float) -> dict[str, Motif]:
    """``{source: Motif}`` for every unambiguous leg-pair group (see the module docstring)."""
    physical = tuple(net for net in nets if net.physical)
    legs_of = {k: tuple(n for n in physical if k in n.members) for k, v in items.items() if v.kind == 'leg'}
    candidates: dict[str, list[_Leg]] = defaultdict(list)
    for midpoint in physical:
        if found := _leg_pair(midpoint, legs_of, items):
            candidates[found[0]].append(found[1])
    used = Counter(k for legs in candidates.values() for upper, lower, _refs in legs for k in (upper, lower))
    reference_owners = Counter(k for legs in candidates.values() for k in {r for _u, _l, refs in legs for r in refs})
    motifs = {}
    for source, legs in sorted(candidates.items()):
        if any(used[k] != 1 for upper, lower, _refs in legs for k in (upper, lower)):
            continue
        legs.sort(key=lambda leg: (items[leg[0]].label or leg[0], leg[0]))
        refs = sorted({r for _u, _l, references in legs for r in references if reference_owners[r] == 1})
        motifs[source] = _pack(source, legs, refs, items, gap)
    return motifs
