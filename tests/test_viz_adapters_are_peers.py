"""The two adapters are EQUAL PEERS: one vocabulary, two contracts, one picture per SHAPE.

NEITHER BACKEND IS THE SECOND-CLASS SPELLING OF THE OTHER, and that is a claim with a measurement
behind it rather than a wording in a docstring: the SAME descriptions -- the five functions
``tests/_viz_figure.py`` shares -- have to come out of both, both have to satisfy the same two
Protocols, and the colour a series takes has to be the same in both. A layer where one backend
silently drew less, or took a different colour, would still pass every per-adapter test.

A SHAPE IS MEASURED BY WHAT BOTH ADAPTERS REPORT, not by the pictures, because that is the layer's
own datum: the same description must resolve to the same rects, the same coordinate systems and the
same rectangle the data covers whichever library is behind it. The two subjects that are NOT driven
through both are the polar frame and the continuum -- bokeh has no polar projection and no
filled-contour glyph over a point set, and it refuses both BY NAME, which ``test_viz_bokeh``
measures. They are named here rather than left for a reader to notice the omission.

BOTH LIBRARIES ARE OPTIONAL EXTRAS, so this module degrades when either is missing: a claim that
holds only when the box happens to have everything installed is not the claim being made.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _viz_figure import _GRID, _SAMPLES, draw_everything, draw_panels, draw_twin

from lab_commons.viz import Figure, Frame

pytest.importorskip('matplotlib')
pytest.importorskip('bokeh')

import matplotlib as mpl
from bokeh import palettes
from bokeh.models import Image

from lab_commons.viz import Series, Style, bokeh
from lab_commons.viz import mpl as viz_mpl


def test_both_adapters_satisfy_the_one_contract() -> None:
    """The Protocols are the contract, and both adapters are checked against the same objects.

    BOTH HALVES: a renderer IS a canvas and a frame IS a coordinate system, so an adapter that
    satisfied one and not the other would be a peer of nothing.
    """
    for renderer in (viz_mpl.MplRenderer(), bokeh.BokehRenderer()):
        assert isinstance(renderer, Figure)
        assert isinstance(renderer.frame(), Frame)


def test_the_same_description_draws_through_both(tmp_path: Path) -> None:
    """One description, two libraries, two artifacts -- and neither is an empty file."""
    raster = viz_mpl.MplRenderer()
    page = bokeh.BokehRenderer()
    for renderer in (raster, page):
        draw_everything(renderer.frame())
    written = [raster.save(tmp_path / 'figure.png'), page.save(tmp_path / 'figure.html')]
    assert [path.suffix for path in written if path is not None] == ['.png', '.html']
    for path in written:
        assert path is not None
        assert path.is_file()
        assert path.stat().st_size > 0


def test_a_frame_cannot_be_shared_across_the_two_adapters() -> None:
    """A frame's axis is ITS library's object, so a share across adapters is refused BY NAME.

    The failure this prevents is a stack trace from inside an adapter: an ``Axes`` handed to bokeh
    and a ``Range1d`` handed to matplotlib both read as "no such attribute" three frames later,
    which says nothing about what the caller did wrong. Both adapters refuse it identically.
    """
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    with pytest.raises(TypeError, match='own adapter'):
        raster.frame(sharex=page.frame())
    with pytest.raises(TypeError, match='own adapter'):
        page.frame(sharex=raster.frame())
    with pytest.raises(TypeError, match='own adapter'):
        raster.frame(rect=(0.0, 0.0, 0.5, 1.0), sharey=page.frame())


def test_the_same_panel_grid_draws_through_both(tmp_path: Path) -> None:
    """FOUR PANELS, TWO LIBRARIES, ONE SET OF FRAMES — the rects and the shares both report.

    The rects are the vocabulary's answer to "where does each panel go", and both adapters report
    the same ones because neither decides it; the shares are each library's own spelling of "one
    limit", which is why the assertion below is about the limit rather than about the mechanism.
    """
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    for renderer in (raster, page):
        draw_panels(renderer)
    assert [frame.rect for frame in raster.frames] == [frame.rect for frame in page.frames]
    assert len(raster.figure.axes) == 4
    assert len(page.frames) == 4
    raster.frames[0].set_limits(x=(0.0, 5.0))
    assert raster.frames[3].axes.get_xlim() == (0.0, 5.0), 'matplotlib lost the share'
    assert page.frames[3].figure.x_range is page.frames[0].figure.x_range, 'bokeh lost the share'
    written = [raster.save(tmp_path / 'panels.png'), page.save(tmp_path / 'panels.html')]
    assert all(path is not None and path.is_file() for path in written)


def test_the_same_twin_axis_draws_through_both(tmp_path: Path) -> None:
    """TWO FRAMES OVER ONE PANEL, TWO LIBRARIES: the same twin, spelled the way each library has.

    Both report two frames at ONE rect, which is what a twin IS; how each library draws the second
    scale -- a second axes with its y on the right against a second y range with an axis on the
    right -- is the library's own business, and each is measured in its own test file.
    """
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    for renderer in (raster, page):
        draw_twin(renderer)
    assert [frame.rect for frame in raster.frames] == [frame.rect for frame in page.frames]
    assert raster.frames[1].rect == raster.frames[0].rect, 'matplotlib drew the twin somewhere else'
    assert page.frames[1].figure is page.frames[0].figure, 'bokeh put the twin in a second panel'
    written = [raster.save(tmp_path / 'twin.png'), page.save(tmp_path / 'twin.html')]
    assert all(path is not None and path.is_file() for path in written)


def test_a_series_takes_the_same_colour_through_either_adapter() -> None:
    """The palette is the vocabulary's, so which library drew the figure cannot change the picture."""
    series = Series(x=[0.0, 1.0], y=[0.0, 1.0], label='phase A')
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    raster.frame().draw_line(series)
    page.frame().draw_line(series)
    assert raster.frames[0].axes.lines[0].get_color() == Style().palette[0]
    assert page.frames[0].figure.renderers[0].glyph.line_color == Style().palette[0]


def test_the_same_grid_draws_over_the_same_rectangle_in_both() -> None:
    """One GRID description, two rasters — the extent the PRIMITIVE resolved, in both libraries.

    The rectangle is the datum both adapters have to agree on, and it is the one thing a producer
    computing a mesh's bounding box can check without looking at a picture: matplotlib is handed the
    extent directly, bokeh's image glyph carries it as a lower-left corner plus a width and a height.
    The VOID travels too — masked in one library, NaN in the other, because neither has the other's
    spelling — so a grid that lost its mask through the peer relationship would red here.
    """
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    for renderer in (raster, page):
        renderer.frame().draw_grid(_GRID)
    image = raster.frames[0].axes.images[0]
    assert image.get_extent() == [0.0, 3.0, 0.0, 2.0]
    glyph = page.frames[0].figure.renderers[0].glyph
    assert isinstance(glyph, Image)
    assert (glyph.x, glyph.y, glyph.dw, glyph.dh) == (0.0, 0.0, 3.0, 2.0), 'the rectangle moved'
    assert image.origin == 'lower', 'matplotlib drew the first row at the top'
    assert glyph.origin == 'bottom_left', 'bokeh drew the first row at the top'


def test_the_same_point_cloud_carries_the_same_values_in_both() -> None:
    """One SAMPLES description, two colour-mapped clouds: the values, the scale and the mark size.

    A cloud whose colour means a third quantity is only readable while both adapters map the same
    numbers through the same range, so the SAMPLE VALUES and the pinned limits are asserted on both
    sides rather than the presence of a scatter.
    """
    raster, page = viz_mpl.MplRenderer(), bokeh.BokehRenderer()
    for renderer in (raster, page):
        renderer.frame().draw_samples(_SAMPLES)
    drawn = raster.frames[0].axes.collections[0]
    plotted = page.frames[0].figure.renderers[0].glyph
    assert list(drawn.get_array()) == [0.0, 1.0, 2.0, 3.0]
    assert list(page.frames[0].figure.renderers[0].data_source.data['value']) == [0.0, 1.0, 2.0, 3.0]
    assert drawn.get_clim() == (0.0, 3.0), 'the vocabulary range did not reach the matplotlib norm'
    assert plotted.fill_color['transform'].low == 0.0, 'the same range did not reach the bokeh mapper'
    assert plotted.fill_color['transform'].high == 3.0


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
