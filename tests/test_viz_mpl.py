"""``lab_commons.viz.mpl`` — the matplotlib adapter, and the family's whole display policy.

MATPLOTLIB IS AN OPTIONAL EXTRA, so this module DEGRADES rather than stranding the suite when the
``viz-mpl`` extra is not installed: a module-scope ``pytest.importorskip`` leaves the file
collectable, which is the shape the kit's own collection census classifies as a DEGRADE rather than
as an error. It is deliberately not a ``skip`` mark, because a mark is not a guard -- the module body
runs before pytest can read one.

NO WINDOW IS EVER OPENED HERE, and Agg is selected for exactly that reason before any figure is
built -- a suite that pops a window on somebody's desktop is a suite that gets run nowhere. It is
also what makes the degrade path testable rather than assumed: under Agg a show CANNOT happen, and
every assertion below is about what happens instead.

THE FRAME VERBS ARE MEASURED ON THE ARTISTS THEY LEAVE, never on the call returning: a verb that
returned without drawing anything would satisfy every protocol and produce an empty picture, which
no signature can catch. THE SHAPES ARE MEASURED THE SAME WAY -- a panel grid by the limits its
frames share, a twin axis by the axes matplotlib actually built under it -- because "the picture has
four panels" is a claim about the figure and not about the code that asked for it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from _viz_figure import (
    _GRID,
    _SAMPLES,
    PANEL_RECTS,
    draw_continuum,
    draw_everything,
    draw_panels,
    draw_polar,
    draw_twin,
)

from lab_commons.viz import Colorbar, Contours, Figure, Frame, Samples, Scale, Series, Style, Ticks

pytest.importorskip('matplotlib')

import matplotlib as mpl

from lab_commons.viz import mpl as viz_mpl

mpl.use('Agg')


def test_the_adapter_satisfies_both_protocols() -> None:
    """The adapter is checked against the SAME contracts the vocabulary declares, canvas and frame."""
    renderer = viz_mpl.MplRenderer()
    assert isinstance(renderer, Figure)
    assert isinstance(renderer.frame(), Frame)
    assert renderer.figure.get_size_inches().tolist() == [8.0, 6.0]
    renderer.close()


def test_the_shared_description_reaches_the_canvas() -> None:
    """Every subject in the shared description leaves an artist behind — measured on the figure.

    A count of artists is the honest reading here: a verb that returned without drawing anything
    would satisfy the protocol and produce an empty picture, which no signature can catch.

    ``images`` AND ``collections`` ARE TWO DIFFERENT DRAWINGS, which is the split this tier now
    carries: the grid map is a RASTER (one image over the array's rectangle) and the point cloud is a
    COLLECTION of marks at their own coordinates — and each draws its own colour bar, so the canvas
    below holds the frame plus four bars rather than the frame plus two.
    """
    renderer = viz_mpl.MplRenderer()
    frame = renderer.frame()
    draw_everything(frame)
    axes = frame.axes
    assert len(axes.lines) >= 4, 'the chart, the waveform traces and the wire are lines'
    assert len(axes.patches) >= 3, 'two winding patches and a coil-side circle'
    assert len(axes.texts) >= 1, 'the label'
    assert len(axes.images) == 1, 'the masked grid map is a raster, not a pile of marks'
    assert axes.collections, 'the point cloud is a collection of marks'
    assert axes.quiver, 'the vector field is its own artist, not a pile of annotations'
    assert len(renderer.figure.axes) == 5, 'the grid and the cloud each owe a colour bar'
    renderer.close()


def test_a_panel_grid_shares_the_axes_every_panel_named() -> None:
    """FOUR FRAMES ON ONE CANVAS, and the sharing measured by what a reader would see.

    A limit set on the first panel is the limit of all four.

    Reading ``get_shared_x_axes().joined(...)`` would be reading matplotlib's bookkeeping; reading
    the LIMITS is reading the picture. Both directions are set, because a share that only propagated
    one way would look linked and compare wrongly.
    """
    renderer = viz_mpl.MplRenderer()
    draw_panels(renderer)
    top_left, top_right, bottom_left, bottom_right = renderer.frames

    assert [frame.rect for frame in renderer.frames] == list(PANEL_RECTS)
    assert len(renderer.figure.axes) == 4, 'four frames are four axes'
    top_left.set_limits(x=(0.0, 5.0), y=(0.0, 9.0))
    for frame in (top_right, bottom_left, bottom_right):
        assert frame.axes.get_xlim() == (0.0, 5.0), 'a panel does not share the x axis it named'
        assert frame.axes.get_ylim() == (0.0, 9.0), 'a panel does not share the y axis it named'
    assert [frame.axes.get_title() for frame in (top_left, top_right)] == ['panel 0', 'panel 1']
    renderer.close()


def test_a_twin_axis_is_a_second_scale_on_the_same_pixels() -> None:
    """THE SHAPE ``twinx`` ASKS FOR, measured on the axes matplotlib built.

    One rect, two axes, the second one's y on the right, no second x axis, and a transparent patch
    so the first frame's drawing is not covered by the one drawn over it.
    """
    renderer = viz_mpl.MplRenderer()
    draw_twin(renderer)
    torque, power = renderer.frames

    assert power.rect == torque.rect, 'a twin is drawn over the frame it shares an x axis with'
    assert len(renderer.figure.axes) == 2, 'a twin is a second axes, not a second figure'
    assert power.axes.get_position().bounds == torque.axes.get_position().bounds
    assert power.axes.yaxis.get_ticks_position() == 'right', 'the second scale overprints the first'
    assert power.axes.yaxis.get_label().get_text() == 'power (kW)'
    assert power.axes.xaxis.get_visible() is False, 'a twin is not a second x axis'
    assert power.axes.patch.get_visible() is False, 'an opaque twin hides the frame under it'

    torque.set_limits(x=(0.0, 5000.0))
    power.set_limits(y=(-1.0, 1.0))
    assert power.axes.get_xlim() == (0.0, 5000.0), 'the twin does not share the x axis'
    assert torque.axes.get_ylim() != (-1.0, 1.0), 'the twin does not have its own y scale'
    renderer.close()


def test_a_named_rect_is_the_axes_box_and_an_unnamed_one_is_the_canvas_own_panel() -> None:
    """The two placement statements, measured on the axes matplotlib built.

    A STATED RECT IS EXACT — a producer computing a panel grid gets those panels, to the fraction.
    A frame that named none gets matplotlib's own subplot geometry, which is the box a normal plot
    leaves for its own labels: drawing that frame as if it had named the whole canvas is what puts
    a title outside the picture, and it is why the canvas decides rather than inventing a rect.
    """
    renderer = viz_mpl.MplRenderer()
    placed = renderer.frame(rect=(0.0, 0.0, 0.5, 1.0))
    unplaced = renderer.frame()
    placed.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0]))
    unplaced.draw_line(Series(x=[0.0, 1.0], y=[1.0, 0.0]))

    assert tuple(placed.axes.get_position().bounds) == (0.0, 0.0, 0.5, 1.0)
    left, bottom, width, height = (float(value) for value in unplaced.axes.get_position().bounds)
    assert (left, bottom, width, height) != (0.0, 0.0, 1.0, 1.0), 'the canvas invented a rect'
    assert left > 0.0, 'the frame the canvas placed is flush with the left edge'
    assert left + width < 1.0, 'the frame the canvas placed is flush with the right edge'
    assert bottom > 0.0, 'the frame the canvas placed is flush with the bottom'
    assert bottom + height < 1.0, 'the frame the canvas placed is flush with the top'
    renderer.close()


def test_a_polar_frame_is_built_as_a_polar_axes() -> None:
    """The projection is the LIBRARY's, so the frame is asked what it became rather than assumed.

    ``axes.name`` is matplotlib's own answer, and the trace is on it: a frame that was built
    cartesian would draw the same numbers as a circle-less spiral, which no protocol can catch.
    """
    renderer = viz_mpl.MplRenderer()
    draw_polar(renderer)
    star = renderer.frames[0]
    assert star.projection == 'polar'
    assert star.axes.name == 'polar'
    assert star.axes.lines, 'the star trace was not drawn'
    assert star.axes.get_title() == 'slot EMF star'
    assert star.rect is None, 'a frame that names no rect is placed by the canvas'
    renderer.close()


def test_the_palette_cursor_belongs_to_the_canvas_and_not_to_a_frame() -> None:
    """Two frames of one canvas take DIFFERENT colours, which is what a twin axis needs.

    On a twin the two frames draw over the same pixels, so a per-frame cursor would give the second
    series the first one's colour -- not a repeated colour, an unreadable one. The cursor is the
    canvas's, so the n-th artist of the figure takes the n-th palette entry whichever frame drew it.
    """
    renderer = viz_mpl.MplRenderer()
    first, second = renderer.frame(), renderer.frame(rect=(0.0, 0.5, 1.0, 0.5))
    first.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0]))
    second.draw_line(Series(x=[0.0, 1.0], y=[1.0, 0.0]))
    assert first.axes.lines[0].get_color() == Style().palette[0]
    assert second.axes.lines[0].get_color() == Style().palette[1]
    renderer.close()


def test_the_new_verbs_land_on_the_figure_they_describe() -> None:
    """Each added verb is measured by the ARTIST it leaves, not by the call returning.

    THE THREE THAT ARE NOT A COUNT OF EXISTING ARTISTS: ticks replace the axis's own locator, the
    contour is a collection with no fill, and a quiet axis (an empty label list) must still be
    tickED -- a figure that answered that with "no ticks" would move the marks the caller kept.
    """
    renderer = viz_mpl.MplRenderer()
    frame = renderer.frame()
    frame.set_ticks(
        x=Ticks(positions=[0.0, 1.0], labels=['a', 'b']),
        y=Ticks(positions=[0.0, 2.0], labels=()),
    )
    frame.draw_contours(Contours(x=[0.0, 1.0, 0.0, 1.0], y=[0.0, 0.0, 1.0, 1.0], values=[0.0, 1.0, 1.0, 2.0]))
    assert [text.get_text() for text in frame.axes.get_xticklabels()] == ['a', 'b']
    assert list(frame.axes.get_yticks()) == [0.0, 2.0]
    assert [text.get_text() for text in frame.axes.get_yticklabels()] == ['', ''], 'the ticks are there'
    assert frame.axes.collections, 'the contour lines are a collection'
    renderer.close()


def test_a_grid_map_is_an_image_that_KEEPS_its_voids() -> None:
    """The masked cells reach the artist masked, and the rectangle is the one the grid stated.

    THE VOID IS THE SUBJECT: an array drawn without its mask shows a value the grid never had, and a
    test that asserted only "an image was drawn" would pass on exactly that defect. The extent and
    the row order are checked together, because a map drawn upside down is still a map.
    """
    renderer = viz_mpl.MplRenderer()
    frame = renderer.frame()
    frame.draw_grid(_GRID)
    image = frame.axes.images[0]
    assert image.origin == 'lower', 'row 0 must be the smallest y, whatever the library defaults to'
    assert image.get_extent() == [0.0, 3.0, 0.0, 2.0], 'the image does not cover the grid it was given'
    mask = np.ma.getmaskarray(image.get_array())
    assert mask[1, 1], 'the void was drawn as a value'
    assert not mask[0, 0], 'a cell with a value was drawn as a void'
    assert image.get_clim() == (0.0, 2.5), 'the pinned scale was not honoured'
    assert len(renderer.figure.axes) == 2, 'the map owes a colour bar'
    renderer.close()


def test_a_point_cloud_is_a_coloured_collection_with_its_scale() -> None:
    """The values ride on the collection, its size is the vocabulary's length SQUARED, and it scales.

    The square is the one unit conversion in this adapter: the vocabulary states a mark's size as a
    length and this library takes its area, so a description that says 6 must not draw a 6-point
    area. Both halves are asserted, because a conversion that silently did nothing would look right
    on every description whose sizes happen to be small.
    """
    renderer = viz_mpl.MplRenderer()
    frame = renderer.frame()
    frame.draw_samples(_SAMPLES)
    cloud = frame.axes.collections[0]
    assert list(cloud.get_array()) == [0.0, 1.0, 2.0, 3.0], 'the values are not what the colour means'
    assert cloud.get_clim() == (0.0, 3.0), 'the pinned scale was not honoured'
    assert list(cloud.get_sizes()) == [36.0], 'the mark size reached the marker as an AREA of 6'
    np.testing.assert_allclose(cloud.get_offsets(), [(0.0, 0.0), (1.0, 1.0), (2.0, 0.5), (3.0, 1.5)])
    assert len(renderer.figure.axes) == 2, 'the cloud owes a colour bar'
    renderer.close()


def test_one_size_per_sample_is_honoured_as_each_mark_its_own() -> None:
    """A bubble map: the size carries a second quantity, so the marks are not all alike."""
    renderer = viz_mpl.MplRenderer()
    frame = renderer.frame()
    frame.draw_samples(Samples(x=[0.0, 1.0], y=[0.0, 1.0], values=[0.0, 1.0], size=[2.0, 5.0]))
    assert list(frame.axes.collections[0].get_sizes()) == [4.0, 25.0]
    renderer.close()


def test_a_field_map_is_drawn_as_a_surface_through_its_samples() -> None:
    """The CONTINUUM shape: drawn as a filled contour over the samples, never as marks at them.

    This is the half the split decided, and the half one adapter cannot draw at all -- see
    ``test_viz_bokeh`` for that refusal. Measured here on the artist: a filled contour is a
    collection matplotlib builds from a triangulation, and there is no image and no offset cloud.
    """
    renderer = viz_mpl.MplRenderer()
    draw_continuum(renderer)
    frame = renderer.frames[0]
    assert frame.axes.collections, 'a filled contour is a collection'
    assert not frame.axes.images, 'a continuum is not a raster'
    assert len(renderer.figure.axes) == 2, 'the surface owes a colour bar'
    renderer.close()


def test_an_unpinned_colour_range_is_refused_rather_than_drawn() -> None:
    """A bar over nothing has no samples to derive a range from, and says so by name."""
    with pytest.raises(ValueError, match='vmin'):
        viz_mpl.MplRenderer().frame().draw_colorbar(Colorbar(scale=Scale(cmap='viridis', label='L')))


def test_saving_writes_a_file_and_opens_no_window(tmp_path: Path) -> None:
    """The batch tail: a path back, a non-empty file, and no window however headless the box."""
    renderer = viz_mpl.MplRenderer()
    draw_everything(renderer.frame())
    out = renderer.save(tmp_path / 'figure.png')
    assert out == tmp_path / 'figure.png'
    assert out is not None
    assert out.is_file()
    assert out.stat().st_size > 0


def test_every_frame_of_a_canvas_reaches_the_saved_file(tmp_path: Path) -> None:
    """A multi-panel canvas is ONE artifact: saving writes the whole figure, not the last frame."""
    renderer = viz_mpl.MplRenderer()
    draw_panels(renderer)
    out = renderer.save(tmp_path / 'panels.png')
    assert out is not None
    assert out.is_file()
    assert out.stat().st_size > 0
    assert len(renderer.figure.axes) == 4, 'the saved figure lost the panels it was asked for'


def test_one_description_takes_the_same_colours_every_time() -> None:
    """The palette position is a function of one figure, not of what an earlier figure did."""
    first, second = viz_mpl.MplRenderer(), viz_mpl.MplRenderer()
    for renderer in (first, second):
        renderer.frame().draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0]))
    assert first.frames[0].axes.lines[0].get_color() == Style().palette[0]
    assert second.frames[0].axes.lines[0].get_color() == Style().palette[0]
    first.close()
    second.close()


def test_a_noninteractive_backend_is_never_talked_into_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process owner's declaration wins: a harness that sets Agg must not get a Tk window.

    MEASURED in the tree this policy was ported from -- a light test run opened a live window on the
    user's desktop because the probe ignored the declared backend.
    """
    monkeypatch.setenv('MPLBACKEND', 'Agg')
    assert viz_mpl.enable_interactive_backend() is False
    assert viz_mpl.is_interactive_backend() is False


def test_saving_and_showing_are_not_alternatives(tmp_path: Path) -> None:
    """A window asked for and unavailable must NOT discard the file.

    The bug this pins: a version of the display tail returned from the show branch before writing,
    so asking for a window produced no output at all -- in a workflow where the figure IS an output.
    """
    figure = viz_mpl.MplRenderer().figure
    out = viz_mpl.show_or_save(figure, tmp_path / 'shown.png', interactive=True)
    assert out == tmp_path / 'shown.png'
    assert out is not None
    assert out.is_file()
    assert viz_mpl.is_interactive_backend() is False, 'this test is only meaningful with no window available'


def test_a_show_with_nothing_to_show_returns_no_path() -> None:
    """``None`` means "there was nothing to write", and a dead canvas is not an exception."""
    assert viz_mpl.show_or_save(viz_mpl.MplRenderer().figure, None, interactive=True) is None


def test_apply_style_is_the_one_place_rcparams_are_set() -> None:
    """The style object maps onto matplotlib's globals in ONE function, and it is reversible."""
    before = dict(mpl.rcParams)
    try:
        viz_mpl.apply_style(Style(font_size=18.0, figure_size=(4.0, 3.0)))
        assert mpl.rcParams['font.size'] == 18.0
        assert list(mpl.rcParams['figure.figsize']) == [4.0, 3.0]
        viz_mpl.apply_style()
        assert mpl.rcParams['font.size'] == Style().font_size
    finally:
        mpl.rcParams.update(before)
