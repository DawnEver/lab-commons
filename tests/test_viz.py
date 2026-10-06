"""``lab_commons.viz`` — the vocabulary, the contract, and the renderer that draws nothing.

THIS FILE IMPORTS NO PLOTTING LIBRARY, which is the layer's own claim being measured rather than
described: the vocabulary is what a producer depends on, so it has to be usable -- and testable --
on a box where matplotlib and bokeh were never installed. IT IS ALSO WHERE THE FRAME RULES ARE
MEASURED, for the same reason: which rect a twin inherits, which coordinate system a frame is built
in and which requests are refused are the vocabulary's and not any adapter's, so they are checked
here, with nothing installed, rather than three times through three libraries.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest
from _viz_figure import draw_continuum, draw_everything, draw_panels, draw_polar, draw_twin

from lab_commons.viz import (
    PROJECTIONS,
    Bars,
    Circle,
    Colorbar,
    Contours,
    Field,
    Figure,
    Frame,
    Grid,
    Label,
    NullFrame,
    NullRenderer,
    Patch,
    Samples,
    Scale,
    Segment,
    Series,
    Style,
    Ticks,
    Vectors,
    panel_rects,
)


def test_the_null_renderer_satisfies_both_protocols() -> None:
    """``isinstance`` against the Protocols is what makes "this is a renderer"/"a frame" an ANSWER.

    Structural and runtime-checkable, so a class that has lost a verb is refused at the check rather
    than at the first producer that happens to call the missing one -- and BOTH halves are checked,
    because the canvas and the coordinate system are two contracts now.
    """
    renderer = NullRenderer()
    assert isinstance(renderer, Figure)
    assert isinstance(renderer.frame(), Frame)


def test_the_null_renderer_accepts_every_primitive_and_writes_nothing(tmp_path: Path) -> None:
    """The default renderer is driven through the WHOLE vocabulary, and no file appears.

    A default that could not be driven through a figure description would push ``if plot:`` branches
    back into every producer, which is the shape this class exists to remove.
    """
    renderer = NullRenderer()
    draw_everything(renderer.frame())
    # THE ONE SUBJECT THE SHARED DESCRIPTION CANNOT CARRY is driven here, because every verb --
    # including the one an adapter refuses to DRAW -- has to be a verb this renderer ACCEPTS, or a
    # batch that declares its figures unconditionally would fail on the box it runs unattended on.
    renderer.frame().draw_contours(Contours(x=[0.0, 1.0], y=[0.0, 1.0], values=[0.0, 1.0]))
    target = tmp_path / 'figure.png'
    assert renderer.save(target) is None, 'a renderer that wrote nothing must not claim a path'
    assert not target.exists(), 'the null renderer wrote a file'
    renderer.show()
    renderer.close()


def test_the_null_renderer_draws_the_shapes_one_axes_cannot(tmp_path: Path) -> None:
    """A panel grid, a twin axis, a polar frame and a continuum are all accepted, nothing is drawn.

    THE SHAPES THAT MADE THIS TIER BE REBUILT, declared here on a box with no plotting library at
    all: totality is not a formality, because the box that draws nothing is exactly the box where a
    figure that CANNOT be declared would go unnoticed until it was run somewhere else. ``draw_field``
    is among them on purpose: bokeh refuses to DRAW a continuum, and a renderer that refused to
    ACCEPT it would move an ``if plot:`` branch back into every producer.
    """
    renderer = NullRenderer()
    draw_panels(renderer)
    draw_twin(renderer)
    draw_continuum(renderer)
    draw_polar(renderer)
    assert len(renderer.frames) == 8, 'four panels, two frames of the twin, the map and the star'
    assert renderer.frames[-1].projection == 'polar'
    assert renderer.save(tmp_path / 'figure.png') is None


def test_the_frame_rules_are_the_vocabularys_and_both_adapters_read_them() -> None:
    """Which rect a frame occupies, and in which coordinate system, resolved with NOTHING installed.

    A TWIN IS SPELLED BY NAMING NO RECT: a frame that shares an x axis and says nothing about where
    it goes is drawn over the frame it shares with. That is the rule, so it is measured here rather
    than through a library -- and because every adapter and :class:`NullRenderer` all call it, this
    is the placement all three were handed.
    """
    renderer = NullRenderer()
    base = renderer.frame()
    twin = renderer.frame(sharex=base)
    panel = renderer.frame(rect=(0.0, 0.0, 0.5, 1.0), sharex=base)
    polar = renderer.frame(projection='polar')

    assert base.rect is None, 'a frame that names no rect had one invented for it'
    assert twin.rect is base.rect, 'a twin was not drawn over the frame it shares its x axis with'
    assert panel.rect == (0.0, 0.0, 0.5, 1.0), 'an explicit rect was not kept'
    assert polar.projection == 'polar'
    assert [frame.projection for frame in (base, twin, panel)] == ['cartesian'] * 3
    assert {'cartesian', 'polar'} == PROJECTIONS, 'the declared set of coordinate systems moved'
    assert isinstance(base, NullFrame)


def test_a_figure_shape_that_cannot_be_expressed_is_refused_by_name() -> None:
    """THE UNSUPPORTED-RAISES ARM, and every message names what to do instead.

    Each of these is a request a producer could plausibly write and that no adapter could honour
    honestly: an unknown coordinate system, an axis shared across two of them, a y-axis share that
    names no rect (placement by inheritance means "drawn over", which is an x relationship), a twin
    on the wrong side, and a rect that is not four numbers. Refused HERE, once, rather than three
    times -- and refused identically on the box that draws nothing.
    """
    renderer = NullRenderer()
    base = renderer.frame()
    side = renderer.frame(rect=(0.0, 0.0, 0.5, 1.0))
    with pytest.raises(ValueError, match='unknown projection'):
        renderer.frame(projection='mercator')
    with pytest.raises(ValueError, match='own coordinate system'):
        renderer.frame(projection='polar', sharex=base)
    with pytest.raises(ValueError, match='must name its rect'):
        renderer.frame(sharey=base)
    with pytest.raises(ValueError, match='share an X axis'):
        renderer.frame(rect=(0.0, 0.0, 1.0, 1.0), sharey=base)
    with pytest.raises(ValueError, match='has its own y'):
        renderer.frame(sharex=side, sharey=base)
    with pytest.raises(ValueError, match='four numbers'):
        renderer.frame(rect=(0.0, 0.0, 1.0))
    assert len(renderer.frames) == 2, 'a refused request must not leave a frame behind'


def test_every_primitive_is_frozen() -> None:
    """A description handed to a renderer is not shared mutable state between producer and drawer."""
    series = Series(x=[0.0, 1.0], y=[0.0, 1.0])
    with pytest.raises(dataclasses.FrozenInstanceError):
        series.label = 'edited'


def test_the_primitives_default_to_the_neutral_choice() -> None:
    """Every optional field means "let the renderer decide", so a minimal description is complete."""
    assert Series(x=[0.0], y=[0.0]).color is None
    assert Series(x=[0.0], y=[0.0]).style == '-'
    assert Series(x=[0.0], y=[0.0]).label is None
    assert Series(x=[0.0], y=[0.0]).alpha == 1.0
    assert Patch(vertices=[[0.0, 0.0]]).color is None
    assert Patch(vertices=[[0.0, 0.0]]).hatch is None
    assert Patch(vertices=[[0.0, 0.0]]).alpha == 1.0
    assert Circle(x=0.0, y=0.0, radius=1.0).edgecolor is None
    assert Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0).arrow is False
    assert Label(x=0.0, y=0.0, text='A').halign == 'center'
    assert Label(x=0.0, y=0.0, text='A').box is False, 'a chip nobody asked for is a box on every label'
    assert Field(x=[0.0], y=[0.0], values=[1.0]).scale is None
    assert Grid(values=[[1.0]]).extent is None, 'an unstated extent means the indices are the axes'
    assert Grid(values=[[1.0]]).scale is None
    assert Samples(x=[0.0], y=[0.0], values=[1.0]).scale is None
    assert Samples(x=[0.0], y=[0.0], values=[1.0]).marker is None
    assert Samples(x=[0.0], y=[0.0], values=[1.0]).size is None
    assert Samples(x=[0.0], y=[0.0], values=[1.0]).alpha == 1.0
    assert Contours(x=[0.0], y=[0.0], values=[1.0]).levels == 10
    assert Contours(x=[0.0], y=[0.0], values=[1.0]).color is None
    assert Vectors(x=[0.0], y=[0.0], u=[1.0], v=[0.0]).scale == 1.0
    assert Ticks(positions=[0.0]).labels is None, 'an unlabelled axis is numbered, not silenced'
    # The ONE field that does not defer: the two libraries' own bar widths disagree, so a None here
    # would be a description that draws a different picture per backend.
    assert Bars(x=[0.0], height=[1.0]).width == 0.8


def test_a_grid_resolves_its_extent_once_for_both_adapters() -> None:
    """The rect a grid covers is the PRIMITIVE's answer, so the two libraries cannot disagree.

    A stated extent is kept as given -- it is the mesh's bounding box, which is where these come from.
    An UNSTATED one means the indices are the coordinates, one cell wide and one cell tall around
    each sample, which is the table case (a q-factor matrix, a heatmap of a DataFrame). Computing
    that in each adapter is how one of them ends up half a cell out with nothing to say which moved.
    """
    stated = Grid(values=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], extent=(0.0, 3.0, 0.0, 2.0))
    assert stated.span() == (0.0, 3.0, 0.0, 2.0)
    assert Grid(values=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]).span() == (-0.5, 2.5, -0.5, 1.5)


def test_a_grid_reads_a_void_as_a_void_however_it_was_spelled() -> None:
    """A masked cell and a NaN are the same statement -- a cell with no value -- and stay one.

    THIS IS THE SHAPE'S OWN SUBJECT: a grid rendered without its mask draws a value it never had, and
    the two spellings a producer may use (a masked array from a mesh, a NaN from a solver) have to
    arrive at both adapters as the same void.
    """
    masked = Grid(values=np.ma.masked_array([[1.0, 2.0], [3.0, 4.0]], mask=[[False, True], [False, False]]))
    nan = Grid(values=[[1.0, float('nan')], [3.0, 4.0]])
    for grid in (masked, nan):
        cells = grid.cells()
        assert cells.shape == (2, 2)
        assert cells.mask[0, 1], 'a void arrived as a value'
        assert not cells.mask[1, 1], 'a cell with a value arrived as a void'
        assert cells[1, 1] == 4.0


def test_a_grid_that_is_not_two_dimensional_is_refused_by_name() -> None:
    """UNSUPPORTED-RAISES, naming the shapes that ARE expressible rather than a shape mismatch."""
    with pytest.raises(ValueError, match='2-D array'):
        Grid(values=[1.0, 2.0, 3.0]).cells()
    with pytest.raises(ValueError, match='2-D array'):
        Grid(values=[1.0, 2.0, 3.0]).span()


def test_a_ticks_empty_label_list_is_not_an_absent_one() -> None:
    """``None`` numbers the ticks and ``()`` silences them, because the two are different figures."""
    silenced = Ticks(positions=[0.0, 1.0], labels=())
    numbered = Ticks(positions=[0.0, 1.0])
    assert silenced.labels == ()
    assert numbered.labels is None
    assert silenced.labels is not None, 'an empty sequence collapsed into "number them"'


def test_a_bar_bands_its_ticks_by_the_rule_both_adapters_read() -> None:
    """The discrete geometry is the PRIMITIVE's, so two adapters cannot draw two pictures of it."""
    edges, centers = Colorbar(scale=Scale(vmin=0.0, vmax=3.0), ticks=[0.0, 1.0, 2.0, 3.0]).bands()
    assert centers == (0.0, 1.0, 2.0, 3.0)
    assert edges == (-0.5, 0.5, 1.5, 2.5, 3.5), 'each band is centred on its tick'
    lone = Colorbar(scale=Scale(vmin=0.0, vmax=1.0), ticks=[5.0]).bands()
    assert lone[0] == (4.5, 5.5), 'a lone tick gets a unit-wide band rather than a zero-width one'


def test_a_continuous_bar_has_no_bands_to_name() -> None:
    """An unsupported combination raises rather than answering with a made-up edge."""
    with pytest.raises(ValueError, match='continuous Colorbar has no bands'):
        Colorbar(scale=Scale(vmin=0.0, vmax=1.0)).bands()


def test_scale_is_what_makes_two_figures_comparable() -> None:
    """The range travels WITH the field, which is the difference between a map and a picture."""
    pinned = Scale(cmap='viridis', label='|B| (T)', vmin=0.0, vmax=2.0)
    assert pinned.vmin == 0.0
    assert pinned.vmax == 2.0
    assert Scale().vmin is None, 'an unpinned scale is derived from the samples, and says so'


def test_the_style_cycles_one_palette_for_every_adapter() -> None:
    """One palette, index-stable: a series keeps its colour whatever drew it and however many ran."""
    style = Style()
    assert len(set(style.palette)) == len(style.palette), 'two series would share a colour'
    assert style.color(0) == style.palette[0]
    assert style.color(len(style.palette)) == style.palette[0], 'the cycle wraps rather than raising'
    assert Style(palette=('#000000', '#ffffff')).color(3) == '#ffffff'


def test_the_style_defaults_are_this_librarys_decision() -> None:
    """The one place the family's figure size, resolution and type size are stated."""
    style = Style()
    assert style.figure_size == (8.0, 6.0)
    assert style.dpi == 100
    assert style.font_size == 10.0
    assert style.font_family[0] == 'Times New Roman'


def test_the_panel_grid_arithmetic_is_one_function() -> None:
    """The rects of N panels, in READING ORDER, with the gaps that keep their labels apart.

    A rect is the axes' BOX and the text around it is drawn OUTSIDE that box, so the gaps are the
    whole point of this function -- a grid whose panels touch draws the upper row's x labels through
    the lower row's title. The numbers asserted here are the invariants a caller depends on rather
    than a restatement of the arithmetic: every panel is the same size, the grid spans the rectangle
    it was given, the gaps are the gaps, and the top row is FIRST because that is how a page reads.

    THE APPROXIMATE COMPARISONS STATE AN ABSOLUTE FLOOR: a rect is in CANVAS FRACTIONS, so 1e-9 of
    the canvas is far below anything a layout could mean and far above the error of summing a few
    floats -- and a bare ``approx`` would take the framework's own floor in whatever unit it guessed.
    """
    rects = panel_rects(2, 3, left=0.0, bottom=0.0, right=1.0, top=1.0, hgap=0.05, vgap=0.1)
    assert len(rects) == 6
    widths = {rect[2] for rect in rects}
    heights = {rect[3] for rect in rects}
    assert len(widths) == 1, 'the panels of one grid are equal boxes'
    assert len(heights) == 1, 'the panels of one grid are equal boxes'
    lefts = [rect[0] for rect in rects[:3]]
    assert lefts == sorted(lefts), 'the top row runs left to right'
    assert len(set(lefts)) == 3, 'two columns of one row are at the same left edge'
    assert rects[3][0] == lefts[0], 'the second row starts at the same left edge'
    assert rects[0][1] > rects[3][1], 'the first rect of the row-major order is the TOP one'
    assert rects[3][1] == pytest.approx(0.0, abs=1e-9), 'the bottom row sits on the bottom margin'
    assert rects[0][1] + rects[0][3] == pytest.approx(1.0, abs=1e-9), 'the top row is under the top margin'
    assert rects[1][0] - (rects[0][0] + rects[0][2]) == pytest.approx(0.05, abs=1e-9), 'the column gap is hgap'
    assert rects[0][1] - (rects[3][1] + rects[3][3]) == pytest.approx(0.1, abs=1e-9), 'the row gap is vgap'
    assert rects[-1][0] + rects[-1][2] == pytest.approx(1.0, abs=1e-9), 'the last column ends at the right edge'


def test_the_panel_helper_refuses_a_grid_that_is_not_a_picture() -> None:
    """Two requests no arithmetic can honour honestly, both refused BY NAME rather than drawn."""
    with pytest.raises(ValueError, match='at least one row'):
        panel_rects(0, 2)
    with pytest.raises(ValueError, match='at least one row'):
        panel_rects(2, 0)
    with pytest.raises(ValueError, match='no area is not a picture'):
        panel_rects(3, 3, left=0.0, bottom=0.0, right=1.0, top=0.3, hgap=0.1, vgap=0.2)
