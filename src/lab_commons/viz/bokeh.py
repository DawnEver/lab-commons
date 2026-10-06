"""The bokeh adapter — importing THIS module is what opts a consumer in.

This is the canvas half of :mod:`lab_commons.viz`: the figure objects, the layout they are arranged
into, the style applied to each of them, the palette cursor its frames share, and
:meth:`BokehRenderer.frame`, which creates the :class:`~lab_commons.viz.bokeh_frame.BokehFrame` every
draw verb belongs to. It is an EQUAL PEER of the matplotlib adapter, not its fallback: the same
vocabulary, the same two protocols, and the same picture. Which adapter a consumer takes is a
dependency decision in its own manifest — the ``viz-bokeh`` extra against the ``viz-mpl`` one — and
neither extra pulls the other's library in. Nothing here imports matplotlib, and nothing in ``mpl.py``
imports bokeh.

WHAT IS DIFFERENT, AND IT IS THE LIBRARY RATHER THAN THE LAYER. Bokeh has no process-global figure
registry, no ``rcParams`` and no dead-canvas failure mode: a style is applied to each figure this
renderer builds rather than to the process, ``show`` opens a browser tab, and the output is HTML. So
there is no counterpart to ``mpl.show_or_save``'s GUI policy here — a policy that guarded against a
failure this library does not have would be a mechanism nobody could check. A headless consumer
saves instead of showing, and the saved artifact is a self-contained page.

A BOKEH FIGURE IS ONE PANEL, SO THE CANVAS ABOVE THE FRAMES IS A LAYOUT. That is the one structural
difference from the other adapter, and it decides how frames are laid out: matplotlib places an axes
at an arbitrary rect, while this library arranges figures in a GRID, so the rects are read for the
grid they describe — one row per distinct bottom, one column per distinct left, top row first. Two
frames whose rects are the same cell are one panel (which is what a twin axis is, and what makes the
bokeh twin a second y range inside its base's figure); two frames whose rects describe no grid this
library can build are REFUSED BY NAME rather than drawn somewhere else.

COLOR MAP NAMES ARE TRANSLATED, NOT ASSUMED, and the translation lives in its own module. A
:class:`~lab_commons.viz.Scale` names a colormap and the two libraries carry overlapping but
different sets of them, so :data:`~lab_commons.viz._bokeh_names.PALETTES` is the NAMED SET of names
both adapters carry and an unknown name RAISES with the list. The dashes, the markers and the text
baselines are translated the same way and live beside it: a table of another library's vocabulary is
data, and a table edited through the module that reads it is a table nobody can review on its own.

ONE PROJECTION THIS LIBRARY DOES NOT HAVE, AND IT REFUSES RATHER THAN IMITATING. Bokeh draws every
glyph in cartesian data units and has no polar projection, so ``projection='polar'`` is refused at
:meth:`BokehRenderer.frame` by name, with the adapter that can draw it as the remedy. A polar figure
is drawn with ``lab_commons.viz.mpl``, which is what the equal-peer relationship means: the same
vocabulary, each backend drawing what its library actually has. THE SAME GOES FOR A THIRD AXIS, one
verb further out: this library has no 3D axes at all, so :meth:`BokehRenderer.frame_3d` is refused by
name with the same remedy — the vocabulary declares the protocol, one peer implements it, and this
one says so rather than drawing a projection of it.

TWO DRAW VERBS ARE REFUSED FOR THE SAME REASON, EACH WITH ITS OWN REMEDY. This library interpolates
a point set only through ``contourpy``, which no extra of this package declares, so ``Contours``
(isolines) and ``Field`` (a surface) are raised per CALL by
:class:`~lab_commons.viz.bokeh_frame.BokehFrame` rather than substituted: the cloud of marks a field
map used to be drawn as here is the ``Samples`` shape now, and a producer asks for it by name.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Final

from bokeh.io import save as bokeh_save
from bokeh.io import show as bokeh_show
from bokeh.layouts import gridplot
from bokeh.models import Axis, GridPlot, LinearAxis, Range1d
from bokeh.models.plots import Plot
from bokeh.plotting import figure
from bokeh.resources import INLINE

from lab_commons.viz import Frame, Frame3D, Style
from lab_commons.viz._bokeh_names import PALETTES, given
from lab_commons.viz._placement import Rect, _covers, _place, _Placement
from lab_commons.viz.bokeh_frame import BokehFrame

__all__ = ['PALETTES', 'BokehRenderer']

#: The coordinate systems this adapter can build, BY NAME — the subset of
#: :data:`~lab_commons.viz.PROJECTIONS` bokeh has a projection for. A named set rather than an
#: ``if projection == 'polar'``: the day the vocabulary grows a third system, this is the line that
#: has to answer for it, and a frame it does not name is refused with the remedy rather than drawn
#: in the wrong units.
_BOKEH_PROJECTIONS: Final = frozenset({'cartesian'})


def _axis_typeface(axis: Axis, style: Style) -> None:
    """Put the style's typeface and type size on one bokeh axis — the panel's or a twin's.

    ONE FUNCTION FOR EVERY AXIS because a twin axis is a real axis of the same panel: a second y
    scale drawn in another font is the drift this layer exists to prevent, and it is one call away
    from happening wherever an axis is created.
    """
    axis.axis_label_text_font = style.font_family[0]
    axis.axis_label_text_font_size = f'{style.font_size}pt'


def _style_plot(plot: Plot, style: Style) -> None:
    """Apply the style to a NEW figure — this library's per-figure spelling of :class:`Style`.

    Bokeh has no process-global table to write into, so unlike the matplotlib adapter's
    ``apply_style`` this is applied to each figure as it is built, from the one :class:`Style` the
    canvas holds: two panels of one figure are typed identically because they were handed the same
    object, not because a global happened to still hold it.
    """
    plot.title.text_font = style.font_family[0]
    plot.title.text_font_size = f'{style.font_size}pt'
    _axis_typeface(plot.xaxis, style)
    _axis_typeface(plot.yaxis, style)


class BokehRenderer:
    """A bokeh layout of one or more figures — THE CANVAS, and the frames a producer creates on it.

    Attributes:
        style: the :class:`~lab_commons.viz.Style` this canvas was built with.
        frames: every frame this canvas holds, in creation order — the escape hatch for a frame a
            producer kept nowhere. There is no single ``figure`` attribute to hand back: this
            library has no object above a figure except a layout, and the layout is what
            :meth:`save` builds.

    """

    def __init__(self, style: Style | None = None) -> None:
        """Build one empty canvas, sizing its panels from the style rather than bokeh's default.

        The style's figure size is inches and bokeh sizes in pixels, so the size is converted at the
        style's own dpi -- one number in the vocabulary, two libraries' units, converted in the one
        place that knows both.

        NO PANEL IS CREATED HERE, for the reason the matplotlib adapter gives: a canvas starts empty
        and every frame is asked for, so a two-panel figure is two frames and a single-panel one is
        one rather than two.
        """
        self.style = Style() if style is None else style
        self._width = int(self.style.figure_size[0] * self.style.dpi)
        self._height = int(self.style.figure_size[1] * self.style.dpi)
        self._drawn: Iterator[int] = count()
        self._frames: list[BokehFrame] = []

    def frame(
        self,
        *,
        rect: Rect | None = None,
        projection: str | None = None,
        sharex: Frame | None = None,
        sharey: Frame | None = None,
    ) -> BokehFrame:
        """Create a coordinate system on this canvas — see :meth:`lab_commons.viz.Figure.frame`.

        THE PLACEMENT IS NOT DECIDED HERE, for the reason the matplotlib adapter's own docstring
        gives: which rect, which projection and whether this is a twin come from the vocabulary's one
        resolution, and this method only translates the answer into bokeh objects.

        A PARTIAL PROJECTION IS REFUSED BY NAME, with the adapter that can draw it as the remedy. A
        frame silently built as cartesian would be a picture that looks drawn and means something
        else -- the one failure this tier exists to make impossible.
        """
        placement = _place(rect=rect, projection=projection, sharex=sharex, sharey=sharey)
        if placement.projection not in _BOKEH_PROJECTIONS:
            msg = (
                f'BokehRenderer cannot build a {placement.projection!r} frame: bokeh draws every glyph in '
                f'cartesian data units and has no {placement.projection} projection. Draw this figure with '
                'lab_commons.viz.mpl.MplRenderer, which has one'
            )
            raise NotImplementedError(msg)
        frame = (
            self._twin_frame(placement)
            if placement.twin_of is not None
            else self._panel_frame(placement, sharex=sharex, sharey=sharey)
        )
        self._frames.append(frame)
        return frame

    def frame_3d(self, *, rect: Rect | None = None) -> Frame3D:
        """REFUSED: this library has no 3D axes — the refusal the module docstring names.

        THE REFUSAL IS HERE RATHER THAN ON A 3D FRAME OF THIS ADAPTER, because there is no such frame
        to hand back: bokeh draws every glyph in two cartesian dimensions and has no third axis, so
        the honest answer is at the door the producer knocks on. It is the same shape as the polar
        refusal one verb above and it names the same remedy, so a consumer learns one rule: this
        adapter draws what its library HAS, and says which adapter draws the rest.

        A ``rect`` is not read and not refused on its own: nothing is built here whatever the
        placement, and a malformed rectangle is :meth:`frame`'s to refuse — this verb never reaches
        the axes it would have been handed.
        """
        msg = (
            'BokehRenderer cannot build a 3D frame: bokeh draws every glyph in two cartesian dimensions '
            'and has no third axis. Draw this figure with lab_commons.viz.mpl.MplRenderer, which has one'
        )
        raise NotImplementedError(msg)

    @property
    def frames(self) -> tuple[BokehFrame, ...]:
        """Every frame this canvas holds, in creation order — the escape hatch for a kept-nowhere one."""
        return tuple(self._frames)

    def save(self, path: Path | str) -> Path | None:
        """Write the figure — every panel of it — as a self-contained HTML page, and return the path.

        The page is resolution independent, which is why the protocol carries no per-call resolution
        at all -- see :meth:`lab_commons.viz.Figure.save`. A caller naming a raster extension gets
        the page this library writes and the REAL path back, never a file that lies about its format.

        THE RESOURCES ARE INLINED, so the artifact stands alone: a file that renders only while the
        box holding it can reach a CDN is a report that stops being readable exactly when it is read
        off the machine that wrote it.

        THE PAGE IS TITLED after the first frame that has a title, or after the file the caller
        named when none does — this library warns when a page is saved with no title at all, and a
        canvas has no title of its own to give (each frame carries its own; see
        :class:`~lab_commons.viz.bokeh_frame.BokehFrame`).
        """
        target = Path(path)
        if target.suffix.lower() != '.html':
            target = target.with_suffix('.html')
        target.parent.mkdir(parents=True, exist_ok=True)
        titled = [frame.figure.title.text for frame in self._frames if frame.figure.title.text]
        bokeh_save(self._layout(), filename=str(target), resources=INLINE, title=titled[0] if titled else target.stem)
        return target

    def show(self, *, interactive: bool = True) -> None:
        """Open the figure in a browser tab; ``interactive=False`` opens nothing at all.

        There is no dead-canvas case to degrade for -- bokeh renders in the browser -- so the only
        question this verb answers is whether a tab opens. It does NOT invent a path to write when
        asked for no window: writing a page is :meth:`save`'s job, and a method that picked its own
        filename would put a file somewhere nobody chose.
        """
        if interactive:
            bokeh_show(self._layout())

    def close(self) -> None:
        """Accepted and ignored, because bokeh holds no process-global figure to release."""

    def _panel_frame(self, placement: _Placement, *, sharex: Frame | None, sharey: Frame | None) -> BokehFrame:
        """A frame on a figure of its own, sharing the ranges of the frames it named.

        SHARING A RANGE IS THIS LIBRARY'S ``sharex``/``sharey``: two figures handed the same
        ``Range1d`` zoom, pan and autoscale together, which is the same one-limit behaviour
        matplotlib's shared axes have. Handing over a COPY would be a picture that looks linked and
        is not.
        """
        for shared in (sharex, sharey):
            if shared is not None and not isinstance(shared, BokehFrame):
                msg = f'a frame can share an axis only with a frame of its own adapter, not {type(shared).__name__}'
                raise TypeError(msg)
        plot = figure(
            width=self._width,
            height=self._height,
            # AN UNSET RANGE IS OMITTED, NOT PASSED AS ``None`` -- bokeh validates ``x_range``/``y_range``
            # against a type that refuses it, which is the same lesson ``given`` was written for.
            **given(
                x_range=None if sharex is None else sharex.figure.x_range,
                y_range=None if sharey is None else sharey.figure.y_range,
            ),
        )
        _style_plot(plot, self.style)
        return BokehFrame(
            figure=plot,
            rect=placement.rect,
            projection=placement.projection,
            style=self.style,
            drawn=self._drawn,
        )

    def _twin_frame(self, placement: _Placement) -> BokehFrame:
        """A second y scale on the figure of the frame this one is drawn over.

        THIS LIBRARY HAS NO SECOND AXES. A twin here is a ``Range1d`` registered in the base figure's
        ``extra_y_ranges``, a ``LinearAxis`` for it added on the right, and every glyph the twin draws
        bound to that range by name -- which is what :class:`BokehFrame` does with the values created
        below. The range is created EMPTY on purpose: bokeh autoscales an extra range from the glyphs
        bound to it, exactly as it does the default one.
        """
        base = placement.twin_of
        if not isinstance(base, BokehFrame):
            msg = f'a frame can be drawn over only a frame of its own adapter, not {type(base).__name__}'
            raise TypeError(msg)
        name = f'y{len(base.figure.extra_y_ranges) + 1}'
        y_range = Range1d()
        base.figure.extra_y_ranges[name] = y_range
        y_axis = LinearAxis(y_range_name=name)
        _axis_typeface(y_axis, self.style)
        base.figure.add_layout(y_axis, 'right')
        return BokehFrame(
            figure=base.figure,
            rect=placement.rect,
            projection=placement.projection,
            style=self.style,
            drawn=self._drawn,
            y_range=y_range,
            y_axis=y_axis,
            y_range_name=name,
        )

    def _layout(self) -> GridPlot:
        """The frames arranged into the grid their rects describe, top row first.

        THE RECTS ARE READ FOR A GRID, NOT FOR A POSITION, and that is this library's shape rather
        than a simplification made here: bokeh places a figure in a layout cell, and a cell is the
        only position it has. A producer's panel grid (equal cells, one rect each) is therefore
        drawn exactly, a twin's shared rect puts both frames in one cell, and anything else is
        refused below rather than drawn in a cell that does not mean it.
        """
        rows = sorted({_covers(frame.rect)[1] for frame in self._frames}, reverse=True)
        columns = sorted({_covers(frame.rect)[0] for frame in self._frames})
        cells: list[list[Plot | None]] = [[None] * len(columns) for _ in rows]
        for frame in self._frames:
            row, column = rows.index(_covers(frame.rect)[1]), columns.index(_covers(frame.rect)[0])
            if cells[row][column] is not None and cells[row][column] is not frame.figure:
                msg = (
                    'two frames cover one canvas cell without sharing a rect, and bokeh has no way to draw '
                    'two coordinate systems in one cell: give them the same rect (a twin axis) or move one '
                    'to a rect of its own. lab_commons.viz.mpl.MplRenderer places each axes at an arbitrary '
                    'rect if the figure needs one'
                )
                raise ValueError(msg)
            cells[row][column] = frame.figure
        return gridplot(cells)
