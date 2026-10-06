"""The bokeh frame — ONE coordinate system on a :class:`~lab_commons.viz.bokeh.BokehRenderer`.

WHY THE TWO HALVES ARE TWO MODULES. ``bokeh.py`` is the canvas (the figure objects, the layout they
are arranged into, the palette cursor and the lifecycle) and this is the coordinate system (one
``bokeh.plotting.figure`` and every verb that draws on it). They are split because they are the two
concepts the tier was rebuilt around, and because the drawn half is the larger one.

A BOKEH FIGURE IS ONE COORDINATE SYSTEM, which is why the canvas above the frames is a layout and not
a figure. That has one consequence worth stating here: A TWIN FRAME IS A SECOND Y RANGE INSIDE ITS
BASE'S FIGURE — this library has no second axes to overlay, so a twin registers a ``Range1d`` in
``figure.extra_y_ranges``, adds a ``LinearAxis`` for it on the right, and binds every glyph it draws
to that range by name. Everything else about the twin (its title, its grid, its legend, whether its
axes are drawn at all) belongs to the shared figure, because in this library that is where those
live.

ONE PLACEMENT THIS LIBRARY CANNOT HONOUR, AND IT REFUSES RATHER THAN MISDRAWING IT. An ARROWED
``Segment`` is drawn here as a bokeh ``Arrow`` ANNOTATION, and an annotation is positioned in the
figure's default ranges — a twin frame's y scale is a different one, so the arrow would land on the
base frame's scale. There is no range binding on an annotation, so the verb is refused by name on a
twin frame instead of drawn in the wrong place.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from bokeh.models import Arrow, FixedTicker, LinearAxis, NormalHead, Plot, Range1d

from lab_commons.viz import (
    Bars,
    Circle,
    Colorbar,
    Contours,
    Field,
    Label,
    Patch,
    Rect,
    Scale,
    Segment,
    Series,
    Style,
    Ticks,
    Vectors,
)
from lab_commons.viz import _bokeh_glyphs as glyphs
from lab_commons.viz._bokeh_names import BASELINES, DASHES, MARKERS, given

__all__ = ['BokehFrame']


class BokehFrame:
    """One coordinate system on a bokeh layout: a figure and every verb that draws on it.

    Attributes:
        figure: the bokeh ``figure`` this frame draws on -- the adapter's own escape hatch, for the
            one call the vocabulary does not carry yet. A twin axis SHARES this object with the frame
            it is drawn over, because in this library one figure is one panel.
        rect: where this frame sits on the canvas, ``(left, bottom, width, height)`` in fractions.
        projection: this frame's coordinate system, always ``'cartesian'`` here -- the canvas refuses
            the other one by name rather than this class pretending.

    """

    def __init__(
        self,
        *,
        figure: Plot,
        rect: Rect | None,
        projection: str,
        style: Style,
        drawn: Iterator[int],
        y_range: Range1d | None = None,
        y_axis: LinearAxis | None = None,
        y_range_name: str | None = None,
    ) -> None:
        """Bind a bokeh figure to the canvas's style and cursor, and to the y scale this frame reads.

        THE Y RANGE AND ITS AXIS ARE ARGUMENTS because a twin frame's are not the figure's defaults:
        the canvas registers the twin's ``Range1d`` and right-hand ``LinearAxis`` and hands them in,
        and every y operation below goes through them. For a frame that stands on its own they are
        the figure's own defaults and ``y_range_name`` stays ``None``, which is the one spelling this
        library reads as "the default range" -- a ``None`` passed through to a glyph would be
        REFUSED by bokeh, so it is dropped rather than sent.
        """
        self.figure = figure
        self.rect = rect
        self.projection = projection
        self._style = style
        self._drawn = drawn
        self._y_range = y_range if y_range is not None else figure.y_range
        self._y_axis = y_axis if y_axis is not None else figure.yaxis
        self._y_range_name = y_range_name
        self._outline = figure.outline_line_color

    def set_title(self, text: str) -> None:
        """Set the panel's title — one title per panel, whichever frame of it asks first."""
        self.figure.title.text = text

    def set_xlabel(self, text: str) -> None:
        """Set this panel's x axis label."""
        self.figure.xaxis.axis_label = text

    def set_ylabel(self, text: str) -> None:
        """Set THIS frame's y axis label — a twin frame's own axis, not the panel's first one."""
        self._y_axis.axis_label = text

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Fix the axis limits; a pair given high-to-low inverts that axis, which bokeh honours."""
        if x is not None:
            self.figure.x_range.start, self.figure.x_range.end = x
        if y is not None:
            self._y_range.start, self._y_range.end = y

    def set_ticks(self, *, x: Ticks | None = None, y: Ticks | None = None) -> None:
        """Place the ticks of either axis at the positions a :class:`Ticks` names.

        AN EMPTY LABEL SEQUENCE IS NOT AN ABSENT ONE. ``None`` leaves the numbers alone; ``()`` asks
        for the tick positions WITH NO TEXT, which this library spells by making the labels
        transparent -- there is no "tick but no label" switch on a bokeh axis, and dropping the
        ticker instead would move the marks the caller asked to keep.
        """
        axes = ((self.figure.xaxis, x), (self._y_axis, y))
        for axis, ticks in axes:
            if ticks is None:
                continue
            axis.ticker = FixedTicker(ticks=[float(position) for position in ticks.positions])
            if ticks.labels is None:
                continue
            axis.major_label_text_alpha = 0 if len(ticks.labels) == 0 else 1
            if len(ticks.labels) > 0:
                axis.major_label_overrides = {
                    float(position): str(text) for position, text in zip(ticks.positions, ticks.labels, strict=True)
                }

    def set_equal_aspect(self, *, on: bool = True) -> None:
        """Match the panel's aspect to the ranges', which is one unit of x per unit of y."""
        self.figure.match_aspect = on

    def set_axis_off(self, *, off: bool = True) -> None:
        """Hide the panel's axes, ticks, grid and frame — one panel, not one frame of it."""
        if off:
            self.figure.axis.visible = False
            self.figure.grid.visible = False
            self.figure.outline_line_color = None
        else:
            self.figure.axis.visible = True
            self.figure.grid.visible = True
            self.figure.outline_line_color = self._outline

    def grid(self, *, on: bool = True) -> None:
        """Show or hide the panel's grid."""
        self.figure.grid.visible = on

    def legend(self, *, on: bool = True) -> None:
        """Show or hide the legend of everything labelled on this panel."""
        self.figure.legend.visible = on

    def draw_line(self, series: Series) -> None:
        """Draw *series* as a line."""
        self.figure.line(
            series.x,
            series.y,
            **given(
                line_dash=DASHES.get(series.style, 'solid'),
                line_width=series.width,
                color=self._next_color(series.color),
                legend_label=series.label,
                line_alpha=series.alpha,
                y_range_name=self._y_range_name,
            ),
        )

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols, with no connecting line."""
        self.figure.scatter(
            series.x,
            series.y,
            **given(
                marker=MARKERS.get(series.marker or 'o', 'circle'),
                size=series.size,
                color=self._next_color(series.color),
                legend_label=series.label,
                line_alpha=series.alpha,
                fill_alpha=series.alpha,
                y_range_name=self._y_range_name,
            ),
        )

    def draw_bars(self, bars: Bars) -> None:
        """Draw *bars* as bars."""
        self.figure.vbar(
            x=bars.x,
            top=bars.height,
            **given(
                width=bars.width,
                color=self._next_color(bars.color),
                legend_label=bars.label,
                fill_alpha=bars.alpha,
                line_alpha=bars.alpha,
                y_range_name=self._y_range_name,
            ),
        )

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when the patches carry one.

        THE FOUR ROWS OF THIS CLASS THAT BUILD DATA RATHER THAN CALLING A GLYPH are implemented in
        :mod:`lab_commons.viz._bokeh_glyphs`, and this is one of them: a region map is a data source
        and a colour mapper here, not a primitive this library has -- see that module for the seam.
        """
        glyphs.patches(self.figure, parts, scale, y_range_name=self._y_range_name)

    def draw_circles(self, circles: Sequence[Circle]) -> None:
        """Draw circles."""
        self.figure.circle(
            x=[circle.x for circle in circles],
            y=[circle.y for circle in circles],
            radius=[circle.radius for circle in circles],
            fill_color=[circle.color if circle.color is not None else None for circle in circles],
            line_color=[circle.edgecolor if circle.edgecolor is not None else None for circle in circles],
            **given(y_range_name=self._y_range_name),
        )

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Draw straight segments, with a head at the end point where one was asked for.

        AN ARROWED SEGMENT IS REFUSED ON A TWIN FRAME, for the reason the module docstring gives: an
        arrow here is an annotation in the figure's default ranges, and a twin frame reads its own.
        The refusal is PER CALL rather than per frame, because every other segment on that frame is
        drawn exactly where it belongs.
        """
        if self._y_range_name is not None and any(segment.arrow for segment in segments):
            msg = (
                'BokehFrame cannot draw an arrowed Segment on a twin axis: bokeh draws an arrow as an '
                "annotation bound to the plot's default y range, and this frame's y range is its own, so "
                'the head would land on the other scale. Draw it on the frame that owns the default range, '
                'or use lab_commons.viz.mpl.MplRenderer for this figure'
            )
            raise NotImplementedError(msg)
        for segment in segments:
            color = self._next_color(segment.color)
            if segment.arrow:
                # Bokeh has no arrow flag on the segment glyph; an annotation IS its arrow.
                self.figure.add_layout(
                    Arrow(
                        end=NormalHead(size=8),
                        x_start=segment.x0,
                        y_start=segment.y0,
                        x_end=segment.x1,
                        y_end=segment.y1,
                        line_color=color,
                    )
                )
            else:
                self.figure.segment(
                    x0=[segment.x0],
                    y0=[segment.y0],
                    x1=[segment.x1],
                    y1=[segment.y1],
                    **given(
                        line_dash=DASHES.get(segment.style, 'solid'),
                        line_width=segment.width,
                        line_color=color,
                        legend_label=segment.label,
                        line_alpha=segment.alpha,
                        y_range_name=self._y_range_name,
                    ),
                )

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Draw text at its anchor, on a chip of its own where one was asked for.

        A CHIP IS FOUR PROPERTIES HERE AND ONE ON THE OTHER SIDE. This library draws a text
        background only when its padding and its fill are both set, and an OMITTED property takes
        bokeh's own default -- so the three are passed together or not at all, which is what
        ``_given`` dropping the ``None`` of an unasked-for chip achieves.
        """
        for label in labels:
            self.figure.text(
                x=[label.x],
                y=[label.y],
                text=[label.text],
                text_font_size=f'{label.size}pt' if label.size is not None else f'{self._style.font_size}pt',
                text_align=label.halign,
                text_baseline=BASELINES.get(label.valign, 'middle'),
                **given(
                    text_color=label.color,
                    padding=6 if label.box else None,
                    background_fill_color='white' if label.box else None,
                    background_fill_alpha=0.7 if label.box else None,
                    y_range_name=self._y_range_name,
                ),
            )

    def draw_field(self, field: Field) -> None:
        """Draw a scalar field and its colour scale — the field map.

        A colour-mapped scatter over a mapper whose range comes from the samples, built in
        :mod:`lab_commons.viz._bokeh_glyphs`: this library's field primitive is an image over a
        rectangular grid, and a mesh's nodes are not one.
        """
        glyphs.field(self.figure, field, y_range_name=self._y_range_name)

    def draw_contours(self, contours: Contours) -> None:
        """REFUSED: this library has no isoline glyph over a point set — see ``bokeh.py``'s docstring.

        The refusal is per-CALL rather than per-import because the alternative is worse in the
        direction that matters: an adapter that quietly drew nothing for this verb would hand back a
        figure that looks finished and has no isolines on it, and the caller has no way to tell that
        from a field whose levels happened to miss. The message names both remedies -- draw the
        contour with the matplotlib adapter, or bring a regular grid this library can contour.
        """
        msg = (
            'BokehFrame cannot draw Contours: bokeh contours a regular 2-D grid and interpolates it with '
            'contourpy, which neither the viz-bokeh extra nor this package declares, so a point set cannot be '
            'honoured. Use lab_commons.viz.mpl.MplRenderer for this figure, or bring a gridded field'
        )
        raise NotImplementedError(msg)

    def draw_vectors(self, vectors: Vectors) -> None:
        """Draw *vectors* as arrows — the SHAFT as one vectorized glyph, the HEAD as another.

        BOKEH HAS NO QUIVER, so the two glyphs are composed in
        :mod:`lab_commons.viz._bokeh_glyphs` rather than one Arrow annotation per sample. The colour
        is taken HERE, before the call, because the palette cursor is the canvas's: a glyph builder
        that reached for a colour would be a second source for the figure's sequence.
        """
        glyphs.vectors(self.figure, vectors, self._next_color(vectors.color), y_range_name=self._y_range_name)

    def draw_colorbar(self, bar: Colorbar) -> None:
        """Draw *bar* — a continuous bar, or one discrete band per tick when ticks are given.

        The range is REFUSED when it is open, for the reason the matplotlib adapter's own docstring
        gives: a bar over nothing has no samples to derive one from, and the alternative to the
        refusal is a legend of unknown extent drawn as if it had one. Built in
        :mod:`lab_commons.viz._bokeh_glyphs`, beside the mappers and tickers it is made of.
        """
        glyphs.colorbar(self.figure, bar)

    def _next_color(self, explicit: str | None) -> str:
        """*explicit* when it is given, else the next palette entry — one slot per drawn artist.

        THE CURSOR IS THE CANVAS'S, shared with every other frame of it: on a twin axis the two
        frames' series occupy the same pixels, where a shared colour is not a repeated colour but an
        unreadable one. The cursor advances whether or not the caller named a colour, so the n-th
        artist's palette position does not depend on which of its predecessors named one.
        """
        index = next(self._drawn)
        return explicit if explicit is not None else self._style.color(index)
