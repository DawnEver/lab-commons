"""The bokeh adapter — importing THIS module is what opts a consumer in.

This is the third half of :mod:`lab_commons.viz`, and it is an EQUAL PEER of the matplotlib one, not
its fallback: the same vocabulary, the same :class:`~lab_commons.viz.Renderer` protocol, and the
same picture. Which adapter a consumer takes is a dependency decision in its own manifest — the
``viz-bokeh`` extra against the ``viz-mpl`` one — and neither extra pulls the other's library in.
Nothing here imports matplotlib, and nothing in ``mpl.py`` imports bokeh.

WHAT IS DIFFERENT, AND IT IS THE LIBRARY RATHER THAN THE LAYER. Bokeh has no process-global figure
registry, no ``rcParams`` and no dead-canvas failure mode: a style is applied to the figure this
renderer builds rather than to the process, ``show`` opens a browser tab, and the output is HTML. So
there is no counterpart to ``mpl.show_or_save``'s GUI policy here — a policy that guarded against a
failure this library does not have would be a mechanism nobody could check. A headless consumer
saves instead of showing, and the saved artifact is a self-contained page.

COLOR MAP NAMES ARE TRANSLATED, NOT ASSUMED, and the translation lives in its own module. A
:class:`~lab_commons.viz.Scale` names a colormap and the two libraries carry overlapping but
different sets of them, so :data:`~lab_commons.viz._bokeh_names.PALETTES` is the NAMED SET of names
both adapters carry and an unknown name RAISES with the list. The dashes, the markers and the text
baselines are translated the same way and live beside it: a table of another library's vocabulary is
data, and a table edited through the module that reads it is a table nobody can review on its own.

ONE VERB THIS LIBRARY CANNOT DRAW, AND IT REFUSES RATHER THAN IMITATING.
:meth:`~lab_commons.viz.Renderer.draw_contours` arrives as a POINT SET and asks for isolines of it.
This library's contour glyph takes a REGULAR GRID -- a 2-D ``z`` -- and its own documentation puts
the interpolation machinery behind a ``contourpy`` dependency that a plain ``bokeh`` install does not
carry, so honouring the verb would mean either re-gridding scattered samples (an interpolation this
adapter has no library for) or importing a package neither the ``viz-bokeh`` extra nor the base
runtime declares. It raises, and the message says which of the two a caller can do about it; a
silent empty render would be a figure that looks drawn and is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import numpy as np
from bokeh.io import save as bokeh_save
from bokeh.io import show as bokeh_show
from bokeh.models import Arrow, ColorBar, ColumnDataSource, FixedTicker, LinearColorMapper, NormalHead
from bokeh.plotting import figure
from bokeh.resources import INLINE
from bokeh.transform import transform

from lab_commons.viz import (
    Bars,
    Circle,
    Colorbar,
    Contours,
    Field,
    Label,
    Patch,
    Scale,
    Segment,
    Series,
    Style,
    Ticks,
    Vectors,
)
from lab_commons.viz._bokeh_names import BASELINES, DASHES, MARKERS, PALETTES, given, palette_for, quantized

__all__ = ['PALETTES', 'BokehRenderer']

#: How large one field sample is drawn, in pixels. Small on purpose: a field arrives as a dense
#: cloud of samples, and a page is zoomable, so the marks are meant to read as a surface rather than
#: as points -- a producer that wants visible markers draws a ``Series`` instead.
_SAMPLE_SIZE_PX: Final = 4

#: The head of one arrow, in pixels. Bokeh's marks are screen-sized, so a vector field drawn here
#: has heads of one size whatever the samples' magnitudes are -- see :meth:`BokehRenderer.draw_vectors`
#: for why that is this library's shape rather than a choice made here.
_VECTOR_HEAD_SIZE: Final = 7

#: Hatch density, MEASURED against the other adapter rather than guessed: with bokeh's own defaults
#: a one-character pattern is drawn far denser than matplotlib draws the same character, so one
#: description would read as two different fills. These two numbers are the pair that makes the
#: hatch marks of the two adapters read alike, and they are here because a reader comparing two
#: figures side by side is the only instrument that can see the difference.
_HATCH_SCALE: Final = 30
_HATCH_WEIGHT: Final = 0.5


class BokehRenderer:
    """A bokeh figure, drawn through the family vocabulary.

    Attributes:
        style: the :class:`~lab_commons.viz.Style` this figure was built with.
        figure: the bokeh ``figure`` itself -- the escape hatch, for the same reason the matplotlib
            adapter has one: a consumer that imported this module has bokeh already.

    """

    def __init__(self, style: Style | None = None) -> None:
        """Build one empty figure, sizing it from the style rather than from bokeh's default.

        The style's figure size is inches and bokeh sizes in pixels, so the size is converted at the
        style's own dpi -- one number in the vocabulary, two libraries' units, converted in the one
        place that knows both.
        """
        self.style = Style() if style is None else style
        width = int(self.style.figure_size[0] * self.style.dpi)
        height = int(self.style.figure_size[1] * self.style.dpi)
        self.figure = figure(width=width, height=height)
        self._outline = self.figure.outline_line_color
        self._drawn = 0
        family = self.style.font_family[0]
        size = f'{self.style.font_size}pt'
        self.figure.title.text_font = family
        self.figure.title.text_font_size = size
        self.figure.xaxis.axis_label_text_font = family
        self.figure.xaxis.axis_label_text_font_size = size
        self.figure.yaxis.axis_label_text_font = family
        self.figure.yaxis.axis_label_text_font_size = size

    def set_title(self, text: str) -> None:
        """Set the figure's title."""
        self.figure.title.text = text

    def set_xlabel(self, text: str) -> None:
        """Set the x axis's label."""
        self.figure.xaxis.axis_label = text

    def set_ylabel(self, text: str) -> None:
        """Set the y axis's label."""
        self.figure.yaxis.axis_label = text

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Fix the axis limits; a pair given high-to-low inverts that axis, which bokeh honours."""
        if x is not None:
            self.figure.x_range.start, self.figure.x_range.end = x
        if y is not None:
            self.figure.y_range.start, self.figure.y_range.end = y

    def set_ticks(self, *, x: Ticks | None = None, y: Ticks | None = None) -> None:
        """Place the ticks of either axis at the positions a :class:`Ticks` names.

        AN EMPTY LABEL SEQUENCE IS NOT AN ABSENT ONE. ``None`` leaves the numbers alone; ``()`` asks
        for the tick positions WITH NO TEXT, which this library spells by making the labels
        transparent -- there is no "tick but no label" switch on a bokeh axis, and dropping the
        ticker instead would move the marks the caller asked to keep.
        """
        for axis, ticks in ((self.figure.xaxis, x), (self.figure.yaxis, y)):
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
        """Match the plot's aspect to the ranges', which is one unit of x per unit of y."""
        self.figure.match_aspect = on

    def set_axis_off(self, *, off: bool = True) -> None:
        """Hide the axes, their ticks, their grid and the frame — a drawing, not a chart."""
        if off:
            self.figure.axis.visible = False
            self.figure.grid.visible = False
            self.figure.outline_line_color = None
        else:
            self.figure.axis.visible = True
            self.figure.grid.visible = True
            self.figure.outline_line_color = self._outline

    def grid(self, *, on: bool = True) -> None:
        """Show or hide the grid."""
        self.figure.grid.visible = on

    def legend(self, *, on: bool = True) -> None:
        """Show or hide the legend of everything labelled so far."""
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
            ),
        )

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when the patches carry one."""
        vertices = [np.asarray(part.vertices, dtype=float) for part in parts]
        values = [part.value for part in parts]
        if scale is not None and any(value is not None for value in values):
            alphas = {part.alpha for part in parts}
            if len(alphas) != 1:
                msg = f'a value-coloured region map carries one transparency for its regions, not {sorted(alphas)}'
                raise ValueError(msg)
            present = [value for value in values if value is not None]
            mapper = LinearColorMapper(
                palette=palette_for(scale.cmap),
                low=scale.vmin if scale.vmin is not None else min(present),
                high=scale.vmax if scale.vmax is not None else max(present),
            )
            source = ColumnDataSource(
                data={
                    'xs': [points[:, 0].tolist() for points in vertices],
                    'ys': [points[:, 1].tolist() for points in vertices],
                    'value': [np.nan if value is None else value for value in values],
                }
            )
            self.figure.patches(
                'xs', 'ys', source=source, fill_color=transform('value', mapper), fill_alpha=parts[0].alpha
            )
            self.figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')
            return
        for points, part in zip(vertices, parts, strict=True):
            self.figure.patches(
                xs=[points[:, 0].tolist()],
                ys=[points[:, 1].tolist()],
                # A COLOUR IS NOT A PROPERTY ``None`` MAY BE DROPPED FROM: bokeh reads ``None`` as
                # "no fill" and "no outline", which is exactly what an unset colour means, while an
                # OMITTED colour would take bokeh's own grey default instead -- a different picture
                # from the one the matplotlib adapter draws for the same description.
                fill_color=part.color,
                line_color=part.edgecolor,
                fill_alpha=part.alpha,
                line_alpha=part.alpha,
                hatch_scale=_HATCH_SCALE,
                hatch_weight=_HATCH_WEIGHT,
                **given(
                    legend_label=part.label,
                    hatch_pattern=part.hatch,
                    hatch_color=part.edgecolor or part.color,
                ),
            )

    def draw_circles(self, circles: Sequence[Circle]) -> None:
        """Draw circles."""
        self.figure.circle(
            x=[circle.x for circle in circles],
            y=[circle.y for circle in circles],
            radius=[circle.radius for circle in circles],
            fill_color=[circle.color if circle.color is not None else None for circle in circles],
            line_color=[circle.edgecolor if circle.edgecolor is not None else None for circle in circles],
        )

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Draw straight segments, with a head at the end point where one was asked for."""
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
                text_font_size=f'{label.size}pt' if label.size is not None else f'{self.style.font_size}pt',
                text_align=label.halign,
                text_baseline=BASELINES.get(label.valign, 'middle'),
                **given(
                    text_color=label.color,
                    padding=6 if label.box else None,
                    background_fill_color='white' if label.box else None,
                    background_fill_alpha=0.7 if label.box else None,
                ),
            )

    def draw_field(self, field: Field) -> None:
        """Draw a scalar field and its colour scale — the field map.

        Bokeh draws a point set as a colour-mapped scatter with a colour bar, which is the glyph it
        has for samples with no grid: the same description the matplotlib adapter sends to a filled
        contour, drawn as marks because this library's field primitive is an image over a rectangular
        grid that a mesh's nodes are not.
        """
        scale = Scale() if field.scale is None else field.scale
        x = np.asarray(field.x, dtype=float)
        y = np.asarray(field.y, dtype=float)
        values = np.asarray(field.values, dtype=float)
        mapper = LinearColorMapper(
            palette=palette_for(scale.cmap),
            low=scale.vmin if scale.vmin is not None else float(np.nanmin(values)),
            high=scale.vmax if scale.vmax is not None else float(np.nanmax(values)),
        )
        source = ColumnDataSource(data={'x': x, 'y': y, 'value': values})
        self.figure.scatter(
            'x',
            'y',
            source=source,
            fill_color=transform('value', mapper),
            size=_SAMPLE_SIZE_PX,
        )
        self.figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')

    def draw_contours(self, contours: Contours) -> None:
        """REFUSED: this library has no isoline glyph over a point set — see the module docstring.

        The refusal is per-CALL rather than per-import because the alternative is worse in the
        direction that matters: an adapter that quietly drew nothing for this verb would hand back a
        figure that looks finished and has no isolines on it, and the caller has no way to tell that
        from a field whose levels happened to miss. The message names both remedies -- draw the
        contour with the matplotlib adapter, or bring a regular grid this library can contour.
        """
        msg = (
            'BokehRenderer cannot draw Contours: bokeh contours a regular 2-D grid and interpolates it with '
            'contourpy, which neither the viz-bokeh extra nor this package declares, so a point set cannot be '
            'honoured. Use lab_commons.viz.mpl.MplRenderer for this figure, or bring a gridded field'
        )
        raise NotImplementedError(msg)

    def draw_vectors(self, vectors: Vectors) -> None:
        """Draw *vectors* as arrows — the SHAFT as one vectorized glyph, the HEAD as another.

        BOKEH HAS NO QUIVER, so this adapter composes the two glyphs it does have rather than
        building one Arrow annotation per sample: a quiver's whole point is that a field of N arrows
        is one artist, and N annotation models would trade the primitive's cost model away for a
        shape it is not worth. The head is therefore a screen-sized marker rather than a head scaled
        with the arrow, which is a difference from the other adapter that a reader can see -- and a
        mark that says "here is the direction" is what the vocabulary promises, not a head length.

        ``|(u, v)| / scale`` is computed HERE, in data units: this library has no scaling of its own
        for a mark, so the arithmetic the vocabulary defines is the arithmetic drawn.
        """
        tip_x = np.asarray(vectors.x, dtype=float) + np.asarray(vectors.u, dtype=float) / vectors.scale
        tip_y = np.asarray(vectors.y, dtype=float) + np.asarray(vectors.v, dtype=float) / vectors.scale
        color = self._next_color(vectors.color)
        self.figure.segment(
            x0=vectors.x,
            y0=vectors.y,
            x1=tip_x,
            y1=tip_y,
            **given(line_width=vectors.width, line_color=color, line_alpha=vectors.alpha),
        )
        self.figure.scatter(
            x=tip_x,
            y=tip_y,
            marker='triangle',
            size=_VECTOR_HEAD_SIZE,
            angle=np.arctan2(np.asarray(vectors.v, dtype=float), np.asarray(vectors.u, dtype=float)) - np.pi / 2,
            **given(fill_color=color, line_color=color, fill_alpha=vectors.alpha, line_alpha=vectors.alpha),
        )

    def draw_colorbar(self, bar: Colorbar) -> None:
        """Draw *bar* — a continuous bar, or one discrete band per tick when ticks are given.

        The range is REFUSED when it is open, for the reason the matplotlib adapter's own docstring
        gives: a bar over nothing has no samples to derive one from, and the alternative to the
        refusal is a legend of unknown extent drawn as if it had one.
        """
        scale = bar.scale
        if scale.vmin is None or scale.vmax is None:
            msg = (
                f'draw_colorbar needs Scale(vmin=..., vmax=...) pinned; got {scale!r}. A bar the figure '
                'draws for itself has no samples to derive a range from'
            )
            raise ValueError(msg)
        if bar.ticks is None:
            mapper = LinearColorMapper(palette=palette_for(scale.cmap), low=scale.vmin, high=scale.vmax)
            self.figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')
            return
        edges, centers = bar.bands()
        mapper = LinearColorMapper(palette=quantized(scale.cmap, len(centers)), low=edges[0], high=edges[-1])
        drawn = ColorBar(color_mapper=mapper, title=scale.label)
        drawn.ticker = FixedTicker(ticks=list(centers))
        if bar.tick_labels is not None:
            drawn.major_label_overrides = {
                center: str(text) for center, text in zip(centers, bar.tick_labels, strict=True)
            }
        self.figure.add_layout(drawn, 'right')

    def save(self, path: Path | str) -> Path | None:
        """Write the figure as a self-contained HTML page and return where it went.

        The page is resolution independent, which is why the protocol carries no per-call resolution
        at all -- see :meth:`lab_commons.viz.Renderer.save`.

        THE RESOURCES ARE INLINED, so the artifact stands alone: a file that renders only while the
        box holding it can reach a CDN is a report that stops being readable exactly when it is read
        off the machine that wrote it.
        """
        target = Path(path)
        if target.suffix.lower() != '.html':
            target = target.with_suffix('.html')
        target.parent.mkdir(parents=True, exist_ok=True)
        bokeh_save(self.figure, filename=str(target), resources=INLINE, title=self.figure.title.text)
        return target

    def show(self, *, interactive: bool = True) -> None:
        """Open the figure in a browser tab; ``interactive=False`` opens nothing at all.

        There is no dead-canvas case to degrade for -- bokeh renders in the browser -- so the only
        question this verb answers is whether a tab opens. It does NOT invent a path to write when
        asked for no window: writing a page is :meth:`save`'s job, and a method that picked its own
        filename would put a file somewhere nobody chose.
        """
        if interactive:
            bokeh_show(self.figure)

    def close(self) -> None:
        """Accepted and ignored, because bokeh holds no process-global figure to release."""

    def _next_color(self, explicit: str | None) -> str:
        """*explicit* when it is given, else the next palette entry — one slot per drawn artist."""
        color = explicit if explicit is not None else self.style.color(self._drawn)
        self._drawn += 1
        return color
