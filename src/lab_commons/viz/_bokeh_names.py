"""The name bokeh knows each vocabulary value by — the translation TABLES, and the three lookups.

WHY THE TABLES ARE NOT IN THE ADAPTER. They are data about somebody else's vocabulary: a colormap
name, a line dash, a marker, a text baseline, every one of them spelled the way THIS library spells
it and not the way :mod:`lab_commons.viz` does. The adapter is the machinery that reads them, and the
seam is the family's own: a table edited through the module that consumes it drifts away from what it
describes, and the diff that adds a row and the diff that checks a row stop being separable. It is
also what keeps the adapter a file of verbs rather than a file of verbs followed by fifty lines of
somebody else's vocabulary — which is the module-size band's own argument, made once, here.

EVERY NAME WAS MEASURED AGAINST THE INSTALLED LIBRARY RATHER THAN ASSUMED. ``test_viz_bokeh`` and
``test_viz_adapters_are_peers`` read both registries — bokeh's palette table and matplotlib's
colormap table — and red on a name either library does not carry, so a row here is checked against
the libraries rather than against the file it lives in.

A NAME THAT IS NOT HERE IS REFUSED BY NAME, never replaced by a default. A silent substitution would
draw a figure whose colour scale or dash pattern differs from the one the description asked for,
which is the one thing a shared vocabulary must never do.
"""

from __future__ import annotations

from typing import Final

from bokeh import palettes

__all__ = ['BASELINES', 'DASHES', 'MARKERS', 'PALETTES', 'given', 'palette_for', 'quantized']

#: The colormaps BOTH adapters carry: the vocabulary's name -> bokeh's palette name. A named set
#: rather than a fallback, because a silent substitution is a colour scale that means something
#: different from the one the description asked for -- and the key is matplotlib's own spelling,
#: MEASURED against its registry rather than assumed, because that is the name a
#: :class:`~lab_commons.viz.Scale` carries and the one the other adapter hands its library. What is
#: NOT here is a scale only one library has (a sequential rainbow, for one), and asking for it
#: raises with this list rather than drawing a different picture.
PALETTES: Final[dict[str, str]] = {
    'Blues': 'Blues256',
    'Greens': 'Greens256',
    'Reds': 'Reds256',
    'cividis': 'Cividis256',
    'gray': 'Greys256',
    'grey': 'Greys256',
    'inferno': 'Inferno256',
    'magma': 'Magma256',
    'plasma': 'Plasma256',
    'turbo': 'Turbo256',
    'viridis': 'Viridis256',
}

#: matplotlib's line-style spelling -> bokeh's. Named rather than derived: the two libraries use
#: words for dashes that share no pattern with each other.
DASHES: Final[dict[str, str]] = {
    '-': 'solid',
    '--': 'dashed',
    '-.': 'dashdot',
    ':': 'dotted',
}

#: The symbols a :class:`~lab_commons.viz.Series` may name, as matplotlib spells them.
MARKERS: Final[dict[str, str]] = {
    '+': 'cross',
    'o': 'circle',
    's': 'square',
    '^': 'triangle',
    'd': 'diamond',
    'v': 'inverted_triangle',
    'x': 'x',
}

#: Text anchors, matplotlib's spelling -> bokeh's. Only the vertical names differ.
BASELINES: Final[dict[str, str]] = {'center': 'middle', 'top': 'top', 'bottom': 'bottom'}


def palette_for(name: str) -> str:
    """Bokeh's palette name for *name*, or a refusal that lists what this adapter can draw."""
    try:
        return PALETTES[name]
    except KeyError as missing:
        msg = f'no bokeh palette named {name!r}; this adapter carries {sorted(PALETTES)}'
        raise ValueError(msg) from missing


def quantized(name: str, bands: int) -> list[str]:
    """*bands* colours sampled at the CENTRES of *name*'s ramp — a discrete bar's palette.

    The sampling positions are ``(index + 0.5) / bands``, which is the same arithmetic the
    matplotlib adapter hands its own colormap, so "one colour per band" means the same colour on
    both sides of a figure comparison. Read out of bokeh's registry rather than assumed: the
    palette NAME :func:`palette_for` resolves is ``<family><width>``, and this asks for the width
    the name spells instead of trusting whatever the family's default happens to be.
    """
    resolved = palette_for(name)
    ramp = palettes.all_palettes[resolved[:-3]][int(resolved[-3:])]
    return [ramp[int((index + 0.5) * len(ramp) / bands)] for index in range(bands)]


def given(**properties: object) -> dict[str, object]:
    """The glyph properties that were actually GIVEN, with ``None`` removed.

    THIS IS THE THIRD TRANSLATION, and it is here for the reason the first two are: ``None`` is the
    vocabulary's spelling for "let the renderer decide", and bokeh's spelling for the same thing is
    to OMIT the property -- it validates a numeric property against ``Real`` and refuses ``None``
    outright, as this adapter learned by drawing a line with no width. One rule, one file, instead
    of a decision re-taken at every glyph call site.
    """
    return {name: value for name, value in properties.items() if value is not None}
