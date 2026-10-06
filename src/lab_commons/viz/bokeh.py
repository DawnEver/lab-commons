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

COLOR MAP NAMES ARE TRANSLATED, NOT ASSUMED. A :class:`~lab_commons.viz.Scale` names a colormap, and
the two libraries carry overlapping but different sets of them, so :data:`PALETTES` is the NAMED SET
of names both adapters carry and an unknown name RAISES with the list. Quietly falling back to a
default would draw a figure whose legend-free colour scale differs from the one another adapter
draws for the same description, which is the one thing a shared vocabulary must never do.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import numpy as np
from bokeh.io import save as bokeh_save
from bokeh.io import show as bokeh_show
from bokeh.models import Arrow, ColorBar, ColumnDataSource, LinearColorMapper, NormalHead
from bokeh.plotting import figure
from bokeh.resources import INLINE
from bokeh.transform import transform

from lab_commons.viz import Bars, Circle, Field, Label, Patch, Scale, Segment, Series, Style

__all__ = ['PALETTES', 'BokehRenderer']

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
_LINE_DASHES: Final[dict[str, str]] = {
    '-': 'solid',
    '--': 'dashed',
    '-.': 'dashdot',
    ':': 'dotted',
}

#: The symbols a :class:`~lab_commons.viz.Series` may name, as matplotlib spells them.
_MARKERS: Final[dict[str, str]] = {
    '+': 'cross',
    'o': 'circle',
    's': 'square',
    '^': 'triangle',
    'd': 'diamond',
    'v': 'inverted_triangle',
    'x': 'x',
}

#: Text anchors, matplotlib's spelling -> bokeh's. Only the vertical names differ.
_BASELINES: Final[dict[str, str]] = {'center': 'middle', 'top': 'top', 'bottom': 'bottom'}

#: How large one field sample is drawn, in pixels. Small on purpose: a field arrives as a dense
#: cloud of samples, and a page is zoomable, so the marks are meant to read as a surface rather than
#: as points -- a producer that wants visible markers draws a ``Series`` instead.
_SAMPLE_SIZE_PX: Final = 4


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
            **_given(
                line_dash=_LINE_DASHES.get(series.style, 'solid'),
                line_width=series.width,
                color=self._next_color(series.color),
                legend_label=series.label,
            ),
        )

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols, with no connecting line."""
        self.figure.scatter(
            series.x,
            series.y,
            **_given(
                marker=_MARKERS.get(series.marker or 'o', 'circle'),
                size=series.size,
                color=self._next_color(series.color),
                legend_label=series.label,
            ),
        )

    def draw_bars(self, bars: Bars) -> None:
        """Draw *bars* as bars."""
        self.figure.vbar(
            x=bars.x,
            top=bars.height,
            **_given(width=bars.width, color=self._next_color(bars.color), legend_label=bars.label),
        )

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when the patches carry one."""
        vertices = [np.asarray(part.vertices, dtype=float) for part in parts]
        values = [part.value for part in parts]
        if scale is not None and any(value is not None for value in values):
            present = [value for value in values if value is not None]
            mapper = LinearColorMapper(
                palette=_colormap(scale.cmap),
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
            self.figure.patches('xs', 'ys', source=source, fill_color=transform('value', mapper))
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
                **_given(legend_label=part.label),
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
                    **_given(
                        line_dash=_LINE_DASHES.get(segment.style, 'solid'),
                        line_width=segment.width,
                        line_color=color,
                        legend_label=segment.label,
                    ),
                )

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Draw text at its anchor."""
        for label in labels:
            self.figure.text(
                x=[label.x],
                y=[label.y],
                text=[label.text],
                text_font_size=f'{label.size}pt' if label.size is not None else f'{self.style.font_size}pt',
                text_align=label.halign,
                text_baseline=_BASELINES.get(label.valign, 'middle'),
                **_given(text_color=label.color),
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
            palette=_colormap(scale.cmap),
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


def _given(**properties: object) -> dict[str, object]:
    """The glyph properties that were actually GIVEN, with ``None`` removed.

    ``None`` is the vocabulary's spelling for "let the renderer decide", and bokeh's spelling for the
    same thing is to OMIT the property -- it validates a numeric property against ``Real`` and
    refuses ``None`` outright, as this adapter learned by drawing a line with no width. So the
    translation happens here once, instead of at every glyph call site.
    """
    return {name: value for name, value in properties.items() if value is not None}


def _colormap(name: str) -> str:
    """Bokeh's palette name for *name*, or a refusal that lists what this adapter can draw."""
    try:
        return PALETTES[name]
    except KeyError as missing:
        msg = f'no bokeh palette named {name!r}; this adapter carries {sorted(PALETTES)}'
        raise ValueError(msg) from missing
