"""``lab_commons.viz.bokeh`` — the bokeh adapter, an EQUAL PEER of the matplotlib one.

BOKEH IS AN OPTIONAL EXTRA, so this module degrades rather than stranding the suite when
``viz-bokeh`` is not installed -- the module-scope ``pytest.importorskip`` shape, for the reason
``test_viz_mpl.py`` gives.

NOTHING HERE OPENS A BROWSER. ``show`` is only ever called with ``interactive=False``, and the
artifact every assertion is taken on is the saved page.

THE PANEL LAYOUT IS MEASURED ON THE GRID THE PAGE IS BUILT FROM, because this library has no axes
rect: a frame becomes one figure in one cell, so "the four panels are where the rects say" is a
question about the layout model and not about the code that asked for it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from _viz_figure import _GRID, _SAMPLES, PANEL_RECTS, draw_everything, draw_panels, draw_twin

from lab_commons.viz import (
    Colorbar,
    Contours,
    Field,
    Figure,
    Frame,
    Samples,
    Scale,
    Segment,
    Series,
    Style,
    Ticks,
    Vectors,
)

pytest.importorskip('bokeh')

from bokeh import palettes
from bokeh.models import Arrow, Image

from lab_commons.viz.bokeh import PALETTES, BokehRenderer


def test_the_adapter_satisfies_both_protocols() -> None:
    """The adapter is checked against the SAME contracts the vocabulary declares, canvas and frame."""
    renderer = BokehRenderer()
    assert isinstance(renderer, Figure)
    assert isinstance(renderer.frame(), Frame)
    assert renderer.style.figure_size == Style().figure_size


def test_the_shared_description_reaches_the_page(tmp_path: Path) -> None:
    """Every subject in the shared description leaves a glyph behind, and the page is written."""
    renderer = BokehRenderer()
    frame = renderer.frame()
    draw_everything(frame)
    assert len(frame.figure.renderers) >= 6, 'bars, lines, markers, patches, circles, segments'
    assert frame.figure.title.text == 'every primitive'
    out = renderer.save(tmp_path / 'figure.html')
    assert out == tmp_path / 'figure.html'
    assert out is not None
    assert out.is_file()
    assert out.stat().st_size > 0
    assert '<html' in out.read_text(encoding='utf-8'), 'the artifact is not a page'


def test_a_panel_grid_is_arranged_by_the_rects_it_named() -> None:
    """FOUR FRAMES, four cells, and one coordinate system each — measured on the layout.

    ``gridplot`` keeps its cells flat as ``(plot, row, column)`` triples, which is the reading this
    takes: the top row is the TOP of the picture (a row index counts down from the highest bottom
    edge), and the columns run left to right. Sharing here is a shared RANGE object, because that is
    what links two bokeh figures — the same one-limit behaviour, in this library's own spelling.
    """
    renderer = BokehRenderer()
    draw_panels(renderer)
    top_left, top_right, bottom_left, bottom_right = renderer.frames

    cells = {(row, column): plot for plot, row, column in renderer._layout().children}
    assert cells == {
        (0, 0): top_left.figure,
        (0, 1): top_right.figure,
        (1, 0): bottom_left.figure,
        (1, 1): bottom_right.figure,
    }, 'the panels are not in the cells their rects describe'
    assert [frame.rect for frame in renderer.frames] == list(PANEL_RECTS)
    for frame in (top_right, bottom_left, bottom_right):
        assert frame.figure.x_range is top_left.figure.x_range, 'a panel does not share the x range it named'
        assert frame.figure.y_range is top_left.figure.y_range, 'a panel does not share the y range it named'


def test_a_twin_axis_is_a_second_y_range_inside_one_figure() -> None:
    """THE SHAPE ``twinx`` ASKS FOR: one figure, two y ranges, its own right-hand axis.

    Every glyph of the twin is bound to its own range BY NAME, and the base's own glyph must stay
    on the default one — which is the half that makes the two scales independent rather than merely
    drawn together.

    The base's own glyph must stay on the DEFAULT range, which is the half that makes the two
    scales independent rather than merely drawn together.
    """
    renderer = BokehRenderer()
    draw_twin(renderer)
    torque, power = renderer.frames

    assert power.figure is torque.figure, 'a twin is a second scale in one figure, not a second figure'
    assert power.rect == torque.rect, 'a twin is drawn over the frame it shares an x axis with'
    assert list(torque.figure.extra_y_ranges) == ['y1'], 'the twin registered no second y range'
    assert torque.figure.right[0].axis_label == 'power (kW)', 'the twin has no axis of its own on the right'
    assert torque.figure.renderers[0].y_range_name == 'default', 'the base glyph left the default range'
    assert power.figure.renderers[1].y_range_name == 'y1', 'the twin glyph is not bound to the twin range'

    torque.set_limits(x=(0.0, 5000.0))
    power.set_limits(y=(0.0, 500.0))
    assert torque.figure.x_range.start == 0.0, 'the twin does not share the x range'
    assert power.figure.extra_y_ranges['y1'].end == 500.0, 'the twin has no y scale of its own'
    assert torque.figure.y_range.end != 500.0, 'the twin wrote its scale into the base frame'


def test_an_arrowed_segment_on_a_twin_is_refused_by_name() -> None:
    """THE ONE PLACEMENT THIS LIBRARY CANNOT HONOUR, pinned as a refusal rather than left to a caller.

    An arrowed segment is an ``Arrow`` annotation here, positioned in the figure's DEFAULT ranges —
    on a twin frame the head would land on the other scale. There is no range binding on an
    annotation, so the honest outcome is a raise that names the remedy, never an arrow in the wrong
    place with a caption nobody can read.
    """
    renderer = BokehRenderer()
    draw_twin(renderer)
    torque, power = renderer.frames
    with pytest.raises(NotImplementedError, match='twin axis'):
        power.draw_segments((Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0, arrow=True),))
    torque.draw_segments((Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0, arrow=True),))
    assert any(isinstance(item, Arrow) for item in torque.figure.center), 'the base frame lost its arrow'


def test_a_polar_frame_is_refused_by_name() -> None:
    """THE ONE PROJECTION THIS LIBRARY DOES NOT HAVE, refused where the frame would be built.

    Bokeh draws every glyph in cartesian data units, so a polar frame silently built as cartesian
    would be a picture that looks drawn and means something else -- the remedy is the other adapter,
    which has the projection, and the message says so.
    """
    with pytest.raises(NotImplementedError, match='polar'):
        BokehRenderer().frame(projection='polar')


def test_two_frames_that_cover_one_cell_without_sharing_it_are_refused(tmp_path: Path) -> None:
    """A rect that is not a grid cell cannot be drawn here.

    The writer says so rather than putting one panel wherever a cell happens to be.
    """
    renderer = BokehRenderer()
    renderer.frame(rect=(0.0, 0.0, 1.0, 0.5))
    renderer.frame(rect=(0.0, 0.0, 0.5, 1.0))
    with pytest.raises(ValueError, match='one canvas cell'):
        renderer.save(tmp_path / 'figure.html')


def test_a_raster_extension_is_rewritten_to_the_format_this_library_writes(tmp_path: Path) -> None:
    """A caller naming ``.png`` gets a page and the REAL path back, never a file that lies."""
    out = BokehRenderer().save(tmp_path / 'figure.png')
    assert out == tmp_path / 'figure.html'
    assert out is not None
    assert out.is_file()


def test_every_palette_name_this_adapter_carries_is_a_palette_bokeh_has() -> None:
    """Each row resolves in bokeh's own registry — family and width, read rather than assumed.

    Read off the registry instead of the module's attributes, because an attribute that does not
    exist fails at the ONE figure that used it; this fails here, for every row, with the name.
    """
    missing = sorted(
        name
        for name in PALETTES.values()
        # every width in this table is three digits; the family is what precedes them
        if int(name[-3:]) not in palettes.all_palettes.get(name[:-3], {})
    )
    assert len(PALETTES) >= 10, 'a floor: an empty table would pass this check vacuously'
    assert not missing, f'bokeh has no such palette: {missing}'


def test_the_new_verbs_land_on_the_page_they_describe() -> None:
    """The added verbs are measured by the GLYPHS behind them, on the bokeh model graph.

    A VECTOR IS TWO GLYPHS HERE rather than one, and that is this library's shape rather than a
    choice made in the adapter: there is no quiver, so the shaft is one vectorized segment renderer
    and the heads are one marker renderer -- never one Arrow annotation per sample, which would
    trade the primitive's whole cost model away.
    """
    renderer = BokehRenderer()
    frame = renderer.frame()
    frame.set_ticks(x=Ticks(positions=[0.0, 1.0], labels=['a', 'b']), y=Ticks(positions=[0.0], labels=()))
    frame.draw_vectors(Vectors(x=[0.0], y=[0.0], u=[1.0], v=[0.0], scale=1.0))
    frame.draw_colorbar(Colorbar(scale=Scale(cmap='viridis', label='L', vmin=0.0, vmax=3.0), ticks=[0.0, 1.0]))
    assert len(frame.figure.renderers) == 2, 'the shaft and the head are two vectorized glyphs'
    assert frame.figure.renderers[1].glyph.marker == 'triangle', 'a shaft with no head is not a vector'
    assert frame.figure.xaxis.ticker.ticks == [0.0, 1.0]
    assert frame.figure.xaxis.major_label_overrides == {0.0: 'a', 1.0: 'b'}
    assert frame.figure.yaxis.major_label_text_alpha == 0, 'an empty label list must silence, not drop, the ticks'
    assert len(frame.figure.right) == 1, 'the colour bar is a layout item on the right'
    renderer.close()


def test_an_unpinned_colour_range_is_refused_rather_than_drawn() -> None:
    """The same refusal as the other adapter's, for the same reason, on the same field."""
    with pytest.raises(ValueError, match='vmin'):
        BokehRenderer().frame().draw_colorbar(Colorbar(scale=Scale(cmap='viridis', label='L')))


def test_a_contour_over_a_point_set_is_refused_by_name() -> None:
    """ONE OF THE VERBS THIS LIBRARY CANNOT DRAW, pinned as a refusal rather than left to a caller.

    Bokeh contours a regular grid and interpolates it with `contourpy`, which neither this package
    nor the `viz-bokeh` extra declares -- so the honest outcome is a raise that names the remedy,
    never a figure that looks drawn and carries no isolines.
    """
    with pytest.raises(NotImplementedError, match='cannot draw Contours'):
        BokehRenderer().frame().draw_contours(Contours(x=[0.0, 1.0], y=[0.0, 1.0], values=[0.0, 1.0]))


def test_a_continuum_is_refused_by_name() -> None:
    """THE OTHER ONE, AND THE SPLIT'S CONSEQUENCE: a ``Field`` is not drawn here AT ALL any more.

    This library has no filled-contour glyph over a point set (the same limitation the contour
    refusal names, one step further out), and the colour-mapped scatter this verb used to draw is
    now the ``Samples`` shape. Keeping the substitution would leave :class:`Field` and
    :class:`Samples` indistinguishable on this adapter -- exactly the conflation the split removed --
    and a producer asking for a contour map would get a cloud that reads as one at a glance. The
    message names both remedies, and the refusal is per CALL: the rest of the figure still draws.
    """
    frame = BokehRenderer().frame()
    field = Field(x=[0.0, 1.0, 0.0, 1.0], y=[0.0, 0.0, 1.0, 1.0], values=[0.0, 1.0, 1.0, 2.0])
    with pytest.raises(NotImplementedError, match='cannot draw Field'):
        frame.draw_field(field)
    frame.draw_samples(Samples(x=[0.0], y=[0.0], values=[1.0]))
    assert frame.figure.renderers, 'the refusal took the rest of the figure down with it'


def test_a_grid_is_an_image_with_TRANSPARENT_voids() -> None:
    """The masked cells reach the mapper's nan colour, and the geometry is the grid's rectangle.

    THE VOID IS THE SUBJECT: a grid drawn without its mask shows a value it never had. Measured on
    the two halves that make it happen -- the array that reaches the glyph carries a NaN where the
    mask was, and the mapper paints a NaN in the fully transparent colour -- plus the geometry, in
    DATA units, of the extent the primitive resolved.
    """
    renderer = BokehRenderer()
    frame = renderer.frame()
    frame.draw_grid(_GRID)
    plotted = frame.figure.renderers[0]
    glyph = plotted.glyph
    assert isinstance(glyph, Image), 'a grid is a raster here, not a cloud of marks'
    assert (glyph.x, glyph.y, glyph.dw, glyph.dh) == (0.0, 0.0, 3.0, 2.0), 'the image lost the extent'
    assert glyph.origin == 'bottom_left', 'row 0 must be the smallest y, whatever the default is'
    assert glyph.color_mapper.nan_color == '#00000000', 'a void drawn as a colour is a value invented'
    image = np.asarray(plotted.data_source.data['image'][0])
    assert np.isnan(image[1, 1]), 'the void did not reach the glyph'
    assert image[0, 0] == 0.0, 'a cell with a value was voided'
    assert len(frame.figure.right) == 1, 'the map owes a colour bar'
    renderer.close()


def test_a_point_cloud_is_a_scatter_over_one_colour_mapper() -> None:
    """The values ride on a colour transform, the size may be per sample, and the scale is drawn.

    A PER-SAMPLE SIZE IS A COLUMN HERE rather than a property, because this library refuses a
    sequence handed to a glyph that has a source -- so the scalar and the per-sample cases are two
    different model shapes and both are asserted.
    """
    renderer = BokehRenderer()
    frame = renderer.frame()
    frame.draw_samples(_SAMPLES)
    plotted = frame.figure.renderers[0]
    assert plotted.glyph.fill_color['field'] == 'value', 'the colour is not the values'
    assert plotted.glyph.size == 6.0, 'the stated mark size did not reach the glyph'
    assert list(plotted.data_source.data['value']) == [0.0, 1.0, 2.0, 3.0]
    assert len(frame.figure.right) == 1, 'the cloud owes a colour bar'

    bubbles = BokehRenderer().frame()
    bubbles.draw_samples(Samples(x=[0.0, 1.0], y=[0.0, 1.0], values=[0.0, 1.0], size=[3.0, 9.0], marker='s'))
    drawn = bubbles.figure.renderers[0]
    assert drawn.glyph.marker == 'square'
    assert drawn.glyph.size == 'size', 'a per-sample size is a column, referenced by field name'
    assert list(drawn.data_source.data['size']) == [3.0, 9.0]
    renderer.close()


def test_a_marker_this_adapter_cannot_draw_is_refused_by_name() -> None:
    """No silent fallback to a circle: that is a figure the other adapter draws differently.

    MEASURED AS A DEFECT BEFORE IT WAS FIXED -- the lookup was ``MARKERS.get(name, 'circle')``, so a
    description asking for a symbol this library has no glyph for came out round. The table's own
    module declares refusal-by-name as its rule, and both verbs that draw a marker now read it.
    """
    with pytest.raises(ValueError, match='no bokeh marker named'):
        BokehRenderer().frame().draw_markers(Series(x=[0.0], y=[0.0], marker='X'))
    with pytest.raises(ValueError, match='no bokeh marker named'):
        BokehRenderer().frame().draw_samples(Samples(x=[0.0], y=[0.0], values=[1.0], marker='X'))


def test_a_colormap_this_adapter_cannot_draw_is_refused_by_name() -> None:
    """No silent fallback to a default: a substitution would draw a different colour scale.

    Measured on the POINT CLOUD, which is the verb here that maps values through a palette -- the
    field map this used to be measured on is refused before it ever reaches the mapper.
    """
    frame = BokehRenderer().frame()
    cloud = Samples(x=[0.0], y=[0.0], values=[1.0], scale=Scale(cmap='definitely-not-a-colormap'))
    with pytest.raises(ValueError, match='no bokeh palette named'):
        frame.draw_samples(cloud)


def test_showing_without_a_window_opens_nothing() -> None:
    """``interactive=False`` invents no path and opens no tab, which is what a headless run wants."""
    renderer = BokehRenderer()
    assert renderer.show(interactive=False) is None
    renderer.close()
