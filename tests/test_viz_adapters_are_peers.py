"""The two adapters are EQUAL PEERS: one vocabulary, one contract, one picture.

NEITHER BACKEND IS THE SECOND-CLASS SPELLING OF THE OTHER, and that is a claim with a measurement
behind it rather than a wording in a docstring: the SAME description -- drawn by the one function
``tests/_viz_figure.py`` shares -- has to come out of both, both have to satisfy the same Protocol,
and the colour a series takes has to be the same in both. A layer where one backend silently drew
less, or took a different colour, would still pass every per-adapter test.

BOTH LIBRARIES ARE OPTIONAL EXTRAS, so this module degrades when either is missing: a claim that
holds only when the box happens to have everything installed is not the claim being made.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _viz_figure import draw_everything

pytest.importorskip('matplotlib')
pytest.importorskip('bokeh')

import matplotlib as mpl
from bokeh import palettes

from lab_commons.viz import Renderer, Series, Style, bokeh
from lab_commons.viz import mpl as viz_mpl


def test_both_adapters_satisfy_the_one_contract() -> None:
    """The Protocol is the contract, and both adapters are checked against the same object."""
    assert isinstance(viz_mpl.MplRenderer(), Renderer)
    assert isinstance(bokeh.BokehRenderer(), Renderer)


def test_the_same_description_draws_through_both(tmp_path: Path) -> None:
    """One description, two libraries, two artifacts -- and neither is an empty file."""
    raster = viz_mpl.MplRenderer()
    page = bokeh.BokehRenderer()
    for renderer in (raster, page):
        draw_everything(renderer)
    written = [raster.save(tmp_path / 'figure.png'), page.save(tmp_path / 'figure.html')]
    assert [path.suffix for path in written if path is not None] == ['.png', '.html']
    for path in written:
        assert path is not None
        assert path.is_file()
        assert path.stat().st_size > 0


def test_a_series_takes_the_same_colour_through_either_adapter() -> None:
    """The palette is the vocabulary's, so which library drew the figure cannot change the picture."""
    series = Series(x=[0.0, 1.0], y=[0.0, 1.0], label='phase A')
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    raster.draw_line(series)
    page.draw_line(series)
    assert raster.figure.axes[0].lines[0].get_color() == Style().palette[0]
    assert page.figure.renderers[0].glyph.line_color == Style().palette[0]


def test_every_colormap_name_is_one_both_libraries_can_draw() -> None:
    """The named set is an INTERSECTION, read off both registries rather than asserted about them.

    Each side is read the way ITS library spells a palette: matplotlib by the name a ``Scale``
    carries, bokeh by the family and width its registry is keyed on. A name neither can draw is
    worse than a refusal -- it is a field map that comes out a different colour per box.
    """
    unknown_to_matplotlib = sorted(name for name in bokeh.PALETTES if name not in mpl.colormaps)
    unknown_to_bokeh = sorted(
        name for name in bokeh.PALETTES.values() if int(name[-3:]) not in palettes.all_palettes.get(name[:-3], {})
    )
    assert not unknown_to_matplotlib, f'matplotlib has no such colormap: {unknown_to_matplotlib}'
    assert not unknown_to_bokeh, f'bokeh has no such palette: {unknown_to_bokeh}'
