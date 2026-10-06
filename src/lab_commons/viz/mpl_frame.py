"""The matplotlib frame — ONE coordinate system on an :class:`~lab_commons.viz.mpl.MplRenderer`.

WHY THE TWO HALVES ARE TWO MODULES. ``mpl.py`` is the canvas (the matplotlib ``Figure``, the style,
the palette cursor, the lifecycle, and the display policy) and this is the coordinate system (an
``Axes`` and every verb that draws on it). They are split because they are the two concepts the
tier was rebuilt around, and because the drawn half is the larger one: the band this repo holds its
modules to is a refactor signal, and the joint it asks for is the one the vocabulary already names.

THE VERBS MOVED RATHER THAN CHANGED. Every spelling and every primitive is the one the renderer
carried before the split — ``draw_line``, ``draw_field``, ``set_ticks`` and the rest — and the only
thing that changed is their owner: a drawing verb belongs to the axes it draws on, which is what
lets one canvas hold several of them.

A TWIN FRAME IS AN ``Axes.twinx()``. When a frame is created over another one sharing its x axis,
this module lets matplotlib build the twin (its own y axis on the right, an invisible x axis, a
transparent patch, the base's y ticks moved left) rather than assembling the same state by hand:
matplotlib's constrained-layout engine keeps a joined twin pair aligned, and a second ``add_axes``
at the same rect would drift apart from its base the moment the layout recomputed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PatchCollection
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
from matplotlib.patches import Circle as CirclePatch
from matplotlib.patches import Polygon as PolygonPatch

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

__all__ = ['MplFrame']


class MplFrame:
    """One coordinate system on a matplotlib figure: an ``Axes`` and every verb that draws on it.

    Attributes:
        axes: the matplotlib ``Axes`` itself -- the adapter's own escape hatch, for the one call the
            vocabulary does not carry yet.
        rect: where this frame sits on the canvas, ``(left, bottom, width, height)`` in fractions —
            or ``None`` when the producer left the placement to the canvas, which drew it as its own
            panel (see :meth:`~lab_commons.viz.mpl.MplRenderer.frame`).
        projection: this frame's coordinate system, one of ``lab_commons.viz.PROJECTIONS``.

    """

    def __init__(
        self,
        *,
        axes: Axes,
        rect: Rect | None,
        projection: str,
        style: Style,
        drawn: Iterator[int],
    ) -> None:
        """Bind an axes to the canvas's style and to the canvas's palette cursor."""
        self.axes = axes
        self.rect = rect
        self.projection = projection
        self._style = style
        self._drawn = drawn

    def set_title(self, text: str) -> None:
        """Set this frame's title."""
        self.axes.set_title(text)

    def set_xlabel(self, text: str) -> None:
        """Set this frame's x axis label."""
        self.axes.set_xlabel(text)

    def set_ylabel(self, text: str) -> None:
        """Set this frame's y axis label."""
        self.axes.set_ylabel(text)

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Fix the axis limits; a pair given high-to-low inverts that axis, which matplotlib honours."""
        if x is not None:
            self.axes.set_xlim(x)
        if y is not None:
            self.axes.set_ylim(y)

    def set_ticks(self, *, x: Ticks | None = None, y: Ticks | None = None) -> None:
        """Place the ticks of either axis at the positions a :class:`Ticks` names.

        THE LABELS ARE READ ON PRESENCE, NOT ON TRUTH: an empty sequence means "these positions and
        no text", which is what an under-labelled index axis asks for, and ``None`` means "number
        them". ``labels or None`` here would collapse the first into the second.
        """
        if x is not None:
            self.axes.set_xticks(np.asarray(x.positions, dtype=float))
            if x.labels is not None:
                self.axes.set_xticklabels([str(label) for label in x.labels])
        if y is not None:
            self.axes.set_yticks(np.asarray(y.positions, dtype=float))
            if y.labels is not None:
                self.axes.set_yticklabels([str(label) for label in y.labels])

    def set_equal_aspect(self, *, on: bool = True) -> None:
        """Draw one unit of x at the same size as one unit of y, so a shape keeps its shape."""
        self.axes.set_aspect('equal' if on else 'auto')

    def set_axis_off(self, *, off: bool = True) -> None:
        """Hide the axes, their ticks and their frame — a drawing, not a chart."""
        if off:
            self.axes.set_axis_off()
        else:
            self.axes.set_axis_on()

    def grid(self, *, on: bool = True) -> None:
        """Show or hide this frame's grid."""
        self.axes.grid(on)

    def legend(self, *, on: bool = True) -> None:
        """Show the legend of everything labelled on this frame, or remove it."""
        existing = self.axes.get_legend()
        if on:
            self.axes.legend()
        elif existing is not None:
            existing.remove()

    def draw_line(self, series: Series) -> None:
        """Draw *series* as a line, taking the next palette colour when it names none."""
        self.axes.plot(
            series.x,
            series.y,
            linestyle=series.style,
            linewidth=series.width,
            color=self._next_color(series.color),
            label=series.label,
            alpha=series.alpha,
        )

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols, with no connecting line."""
        self.axes.plot(
            series.x,
            series.y,
            linestyle='none',
            marker=series.marker or 'o',
            markersize=series.size,
            color=self._next_color(series.color),
            label=series.label,
            alpha=series.alpha,
        )

    def draw_bars(self, bars: Bars) -> None:
        """Draw *bars* as bars."""
        self.axes.bar(
            bars.x,
            bars.height,
            width=bars.width,
            color=self._next_color(bars.color),
            label=bars.label,
            alpha=bars.alpha,
        )

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when the patches carry one.

        VALUE-COLOURED AND NAME-COLOURED ARE DIFFERENT FIGURES, not two spellings of one: a region
        carrying a ``value`` gets a colormap and a colour bar (the region map), and a region carrying
        a ``color`` gets that colour and a legend entry (the identity map).

        THE VALUE PATH TAKES ONE TRANSPARENCY FOR THE WHOLE MAP, which is what a region map has: the
        patches of one scale are one surface, and matplotlib colours them from the COLLECTION. A
        figure whose regions each need their own alpha is drawn with colours and not with values --
        and a description that asks for both at once is refused rather than silently flattened.
        """
        polygons = [PolygonPatch(np.asarray(part.vertices, dtype=float), closed=True) for part in parts]
        values = [part.value for part in parts]
        if scale is not None and any(value is not None for value in values):
            alphas = {part.alpha for part in parts}
            if len(alphas) != 1:
                msg = f'a value-coloured region map carries one transparency for its regions, not {sorted(alphas)}'
                raise ValueError(msg)
            collection = PatchCollection(
                polygons,
                cmap=scale.cmap,
                norm=Normalize(vmin=scale.vmin, vmax=scale.vmax),
                alpha=parts[0].alpha,
            )
            collection.set_array(np.asarray([np.nan if value is None else value for value in values], dtype=float))
            self.axes.add_collection(collection)
            self.axes.autoscale_view()
            self.axes.figure.colorbar(collection, ax=self.axes, label=scale.label)
            return
        for polygon, part in zip(polygons, parts, strict=True):
            polygon.set_facecolor(part.color if part.color is not None else 'none')
            polygon.set_edgecolor(part.edgecolor if part.edgecolor is not None else 'none')
            polygon.set_alpha(part.alpha)
            if part.hatch is not None:
                polygon.set_hatch(part.hatch)
            if part.label is not None:
                polygon.set_label(part.label)
            self.axes.add_patch(polygon)

    def draw_circles(self, circles: Sequence[Circle]) -> None:
        """Draw circles."""
        for circle in circles:
            artist = CirclePatch(
                (circle.x, circle.y),
                circle.radius,
                facecolor=circle.color if circle.color is not None else 'none',
                edgecolor=circle.edgecolor if circle.edgecolor is not None else 'none',
            )
            if circle.label is not None:
                artist.set_label(circle.label)
            self.axes.add_patch(artist)

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Draw straight segments, with a head at the end point where one was asked for."""
        for segment in segments:
            color = self._next_color(segment.color)
            if segment.arrow:
                self.axes.annotate(
                    '',
                    xy=(segment.x1, segment.y1),
                    xytext=(segment.x0, segment.y0),
                    arrowprops={
                        'arrowstyle': '-|>',
                        'color': color,
                        'linewidth': segment.width,
                        'linestyle': segment.style,
                        'alpha': segment.alpha,
                    },
                    label=segment.label,
                )
            else:
                self.axes.plot(
                    (segment.x0, segment.x1),
                    (segment.y0, segment.y1),
                    linestyle=segment.style,
                    linewidth=segment.width,
                    color=color,
                    label=segment.label,
                    alpha=segment.alpha,
                )

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Draw text at its anchor, on a chip of its own where one was asked for."""
        for label in labels:
            self.axes.text(
                label.x,
                label.y,
                label.text,
                color=label.color,
                fontsize=label.size,
                ha=label.halign,
                va=label.valign,
                bbox={'boxstyle': 'round,pad=0.3', 'facecolor': 'white', 'alpha': 0.7} if label.box else None,
            )

    def draw_field(self, field: Field) -> None:
        """Draw a scalar field and its colour scale — the field map.

        The samples go to a FILLED CONTOUR OVER A TRIANGULATION, which is the primitive that accepts
        a point set with no structure: a structured chart, a skewed one and an unstructured mesh all
        arrive here as coordinates and values, and nothing has to be reshaped to a rectangle the
        samples never had.
        """
        scale = Scale() if field.scale is None else field.scale
        artist = self.axes.tricontourf(
            np.asarray(field.x, dtype=float),
            np.asarray(field.y, dtype=float),
            np.asarray(field.values, dtype=float),
            cmap=scale.cmap,
            vmin=scale.vmin,
            vmax=scale.vmax,
        )
        self.axes.figure.colorbar(artist, ax=self.axes, label=scale.label)

    def draw_contours(self, contours: Contours) -> None:
        """Draw *contours* as isolines over a triangulation of the samples, with no fill.

        THE SAME POINT SET :meth:`draw_field` TAKES, drawn the other way: this library computes
        isolines over a Delaunay triangulation of the samples, so a scattered set and a mesh's nodes
        both arrive here unchanged. A colour given is used for EVERY level rather than cycled -- the
        caller is drawing a set of lines that mean one thing, which is what an overlay is.
        """
        self.axes.tricontour(
            np.asarray(contours.x, dtype=float),
            np.asarray(contours.y, dtype=float),
            np.asarray(contours.values, dtype=float),
            levels=contours.levels,
            colors=None if contours.color is None else [contours.color],
            linewidths=contours.width,
            alpha=contours.alpha,
        )

    def draw_vectors(self, vectors: Vectors) -> None:
        """Draw *vectors* as arrows — this library's quiver, ONE artist over every sample.

        ``angles='xy', scale_units='xy'`` pins the arrow to the data coordinates and ``scale`` to the
        vocabulary's meaning (``|(u, v)| / scale`` data units long) instead of matplotlib's own
        default of normalising to the axes. A direction that reads as a number in the description has
        to read as the same number on the page.
        """
        self.axes.quiver(
            np.asarray(vectors.x, dtype=float),
            np.asarray(vectors.y, dtype=float),
            np.asarray(vectors.u, dtype=float),
            np.asarray(vectors.v, dtype=float),
            angles='xy',
            scale_units='xy',
            scale=vectors.scale,
            width=vectors.width,
            color=self._next_color(vectors.color),
            alpha=vectors.alpha,
        )

    def draw_colorbar(self, bar: Colorbar) -> None:
        """Draw *bar* — a continuous bar, or one discrete band per tick when ticks are given.

        THE RANGE IS REFUSED WHEN IT IS OPEN, and that is this adapter's own error rather than the
        vocabulary's: :class:`~lab_commons.viz.Colorbar` says why a bar over nothing cannot derive
        one, and the alternative to this refusal is a legend of unknown extent drawn as if it had
        one -- the declaration-that-lies shape, on the axis a reader trusts most.

        A DISCRETE BAR SAMPLES ITS COLOURS AT THE BAND CENTRES, which mirrors the region maps the
        family draws: the palette is quantised to one colour per tick and the boundaries sit half a
        step outside the outermost ticks, so a tick's label names the band it is centred in.
        """
        scale = bar.scale
        if scale.vmin is None or scale.vmax is None:
            msg = (
                f'draw_colorbar needs Scale(vmin=..., vmax=...) pinned; got {scale!r}. A bar the figure '
                'draws for itself has no samples to derive a range from'
            )
            raise ValueError(msg)
        if bar.ticks is None:
            mappable = ScalarMappable(cmap=scale.cmap, norm=Normalize(vmin=scale.vmin, vmax=scale.vmax))
            mappable.set_array([])
            self.axes.figure.colorbar(mappable, ax=self.axes, label=scale.label)
            return
        edges, centers = bar.bands()
        cmap = ListedColormap(plt.get_cmap(scale.cmap)(np.linspace(0.0, 1.0, len(centers) + 2)[1:-1]))
        mappable = ScalarMappable(cmap=cmap, norm=BoundaryNorm(np.asarray(edges, dtype=float), cmap.N))
        mappable.set_array([])
        drawn = self.axes.figure.colorbar(mappable, ax=self.axes, ticks=centers, label=scale.label)
        if bar.tick_labels is not None:
            drawn.set_ticklabels([str(text) for text in bar.tick_labels])

    def _next_color(self, explicit: str | None) -> str:
        """*explicit* when it is given, else the next palette entry — one slot per drawn artist.

        THE CURSOR IS THE CANVAS'S, shared with every other frame of it. A series of the second panel,
        or of a twin axis drawn over the first, therefore takes the NEXT colour rather than the same
        one: on a twin axis the two series occupy the same pixels, where a shared colour is not a
        repeated colour but an unreadable one.

        The cursor advances whether or not the caller named a colour, so the n-th artist's palette
        position does not depend on which of its predecessors happened to name one: the same figure
        drawn twice takes the same colours.
        """
        index = next(self._drawn)
        return explicit if explicit is not None else self._style.color(index)
