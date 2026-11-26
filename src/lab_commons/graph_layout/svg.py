"""A placed and routed graph as SVG: one rect per box, one path per wire, labels as escaped text.

Generic by design: domain glyphs (a resistor's zigzag, a vendor's port mark) are the caller's adapter's,
drawn over or instead of this. Coordinates pass through unscaled, in the caller's drawing unit.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from html import escape

from lab_commons.graph_layout.geometry import Box, Segment

__all__ = ['layout_svg']


def _number(value: float) -> str:
    if not math.isfinite(value):
        msg = f'layout_svg: a coordinate is not finite ({value!r})'
        raise ValueError(msg)
    text = f'{value:.3f}'.rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


def layout_svg(
    width: float,
    height: float,
    boxes: Iterable[Box],
    wires: Mapping[str, Iterable[Segment]] = {},
    *,
    labels: Mapping[str, str] | None = None,
    font_size: float = 12.0,
    stroke: float = 1.0,
) -> str:
    """The SVG document text. ``wires`` maps a net key to its segments; ``labels`` a box key to its text.

    Boxes and nets are emitted in key order, so equal input gives byte-equal output.
    """
    labels = labels or {}
    n = _number
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(width)}" height="{n(height)}" '
            f'viewBox="0 0 {n(width)} {n(height)}" font-size="{n(font_size)}" stroke-width="{n(stroke)}">'
        )
    ]
    for box in sorted(boxes, key=lambda b: b.key):
        out.append(
            f'<rect data-key="{escape(box.key)}" x="{n(box.x - box.width / 2)}" y="{n(box.y - box.height / 2)}" '
            f'width="{n(box.width)}" height="{n(box.height)}" fill="none" stroke="black"/>'
        )
        if text := labels.get(box.key):
            out.append(
                f'<text x="{n(box.x)}" y="{n(box.y)}" text-anchor="middle" dominant-baseline="central">'
                f'{escape(text)}</text>'
            )
    for key in sorted(wires):
        path = ' '.join(
            f'M{n(s.start.x)},{n(s.start.y)} L{n(s.end.x)},{n(s.end.y)}' for s in wires[key] if s.start != s.end
        )
        if path:
            out.append(f'<path data-net="{escape(key)}" d="{path}" fill="none" stroke="black"/>')
    out.append('</svg>')
    return '\n'.join(out) + '\n'
