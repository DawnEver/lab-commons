"""The bokeh drawings that BUILD DATA, rather than translating one primitive into one glyph.

WHY THESE AND NOT THE OTHER VERBS. Every other verb of a :class:`~lab_commons.viz.bokeh_frame.BokehFrame`
is a call with properties: a line, a scatter, bars, a circle, a segment, a text. These are not — each
ASSEMBLES something this library has no primitive for and passes it as data:

* a **patch set** becomes a ``ColumnDataSource`` of vertex lists, colour-mapped through a
  ``LinearColorMapper`` when the patches carry values, or drawn one by one when they carry a colour;
* a **grid** becomes an ``image`` glyph over a colour mapper whose voids are painted transparent —
  this library's raster, and the reason a masked 2-D array needs no reshape to be drawn here;
* a **point cloud** is a colour-mapped scatter over a mapper whose range comes from the values;
* a **quiver** is TWO glyphs — the shaft as one vectorized segment renderer and the head as one
  marker renderer, the ``|(u, v)| / scale`` arithmetic done here because this library has none;
* a **colour bar** is a mapper, a quantised palette and a fixed ticker, added as a layout item.

That is the seam, and it is the same one :mod:`lab_commons.viz._bokeh_names` is drawn on: the frame
is the VERB TABLE and this is the machinery behind its rows. It is also what the module-size band
asked for — the frame crossed it on these, and splitting them out is a repair rather than a waiver.

THE MEASURED NUMBERS LIVE HERE TOO, beside the only code that reads them: the pixel size of a vector
head (screen-sized in this library), the hatch pair that was measured to make the two adapters' fills
read alike, and the one colour this library will not accept a NAME for.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
from bokeh.models import ColorBar, ColumnDataSource, FixedTicker, LinearColorMapper, Plot
from bokeh.transform import transform
from numpy.typing import ArrayLike

from lab_commons.viz import Colorbar, Grid, Patch, Samples, Scale, Vectors
from lab_commons.viz._bokeh_names import given, marked, palette_for, quantized

#: The head of one arrow, in pixels. Bokeh's marks are screen-sized, so a vector field drawn here
#: has heads of one size whatever the samples' magnitudes are -- see :func:`vectors` for why that is
#: this library's shape rather than a choice made here.
_VECTOR_HEAD_SIZE: Final = 7

#: WHAT A VOID IS PAINTED WITH: a fully transparent colour, in the RGBA-hex spelling this library's
#: Color property accepts. ``'transparent'`` and ``None`` both look like the right spelling and both
#: are REFUSED by it (measured on bokeh 3.10) -- and the first one would be an error only on the
#: figures that HAVE a void, which is the half a test without one never reaches.
_TRANSPARENT: Final = '#00000000'

#: Hatch density, MEASURED against the other adapter rather than guessed: with bokeh's own defaults
#: a one-character pattern is drawn far denser than matplotlib draws the same character, so one
#: description would read as two different fills. These two numbers are the pair that makes the
#: hatch marks of the two adapters read alike, and they are here because a reader comparing two
#: figures side by side is the only instrument that can see the difference.
_HATCH_SCALE: Final = 30
_HATCH_WEIGHT: Final = 0.5


def _mark_size(size: ArrayLike | None) -> float | np.ndarray | None:
    """The mark size as this library's own property: a plain number, or one per sample.

    A 0-D ARRAY IS NOT A NUMBER TO BOKEH. A scalar wrapped in an array arrives at ``scatter`` as a
    sequence literal and is REFUSED ("Columns need to be 1D"), so the vocabulary's "one number, or
    one per sample" is resolved here, at the one place that knows which of the two it was handed.
    """
    if size is None:
        return None
    values = np.asarray(size, dtype=float)
    return float(values) if values.ndim == 0 else values


def patches(figure: Plot, parts: Sequence[Patch], scale: Scale | None, *, y_range_name: str | None) -> None:
    """Draw filled regions; *scale* colours them by value when the patches carry one.

    VALUE-COLOURED AND NAME-COLOURED ARE DIFFERENT FIGURES, not two spellings of one: a region
    carrying a ``value`` goes through a colour mapper as ONE patch collection with a colour bar
    beside it (the region map), and a region carrying a ``color`` is drawn as it is.

    A COLOUR IS NOT A PROPERTY ``None`` MAY BE DROPPED FROM: bokeh reads ``None`` as "no fill" and
    "no outline", which is exactly what an unset colour means, while an OMITTED colour would take
    bokeh's own grey default instead -- a different picture from the one the matplotlib adapter
    draws for the same description.
    """
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
        figure.patches(
            'xs',
            'ys',
            source=source,
            fill_color=transform('value', mapper),
            fill_alpha=parts[0].alpha,
            **given(y_range_name=y_range_name),
        )
        figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')
        return
    for points, part in zip(vertices, parts, strict=True):
        figure.patches(
            xs=[points[:, 0].tolist()],
            ys=[points[:, 1].tolist()],
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
                y_range_name=y_range_name,
            ),
        )


def grid(figure: Plot, cells: Grid, *, y_range_name: str | None) -> None:
    """Draw a scalar on a rectilinear grid and its colour scale — the maskable map.

    AN IMAGE, NOT A SCATTER, and that is what makes this a different picture from a
    :class:`~lab_commons.viz.Samples`: the grid's rectangle becomes the glyph's geometry in DATA
    units — ``x``/``y`` are its lower-left corner, ``dw``/``dh`` its width and height — and the array
    is resampled across it, where a cloud's marks are drawn one per sample at their own coordinates.

    THE VOIDS ARE TRANSPARENT. This library paints a NaN through the colour mapper's ``nan_color``,
    so a masked cell is filled with NaN here and that colour is the fully transparent one; a masked
    cell drawn as a value would be the defect the shape exists to prevent.

    ``origin='bottom_left'`` IS STATED RATHER THAN INHERITED: it is the convention
    :class:`~lab_commons.viz.Grid` defines (row 0 at the smallest y) and this library's own default
    happens to agree today — which is exactly why the description should not rest on it.
    """
    scale = Scale() if cells.scale is None else cells.scale
    values = np.ma.filled(cells.cells(), np.nan)
    x0, x1, y0, y1 = cells.span()
    mapper = LinearColorMapper(
        palette=palette_for(scale.cmap),
        low=scale.vmin if scale.vmin is not None else float(np.nanmin(values)),
        high=scale.vmax if scale.vmax is not None else float(np.nanmax(values)),
        nan_color=_TRANSPARENT,
    )
    figure.image(
        image=[values],
        x=x0,
        y=y0,
        dw=x1 - x0,
        dh=y1 - y0,
        color_mapper=mapper,
        origin='bottom_left',
        **given(y_range_name=y_range_name),
    )
    figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')


def samples(figure: Plot, cloud: Samples, *, y_range_name: str | None) -> None:
    """Draw points coloured by the values they carry — the colour-mapped point cloud.

    A SCATTER OVER A MAPPER, which is what this library has for marks whose colour is a third
    quantity per point: the mapper's range comes from the values unless the scale pins it, and the
    colour bar beside the panel is what makes those colours mean something.

    THE MARKER IS TRANSLATED, NOT DEFAULTED — :func:`~lab_commons.viz._bokeh_names.marked` refuses a
    name this library has no glyph for, so a description drawn as squares by one adapter cannot
    arrive here as circles.
    """
    scale = Scale() if cloud.scale is None else cloud.scale
    values = np.asarray(cloud.values, dtype=float)
    mapper = LinearColorMapper(
        palette=palette_for(scale.cmap),
        low=scale.vmin if scale.vmin is not None else float(np.nanmin(values)),
        high=scale.vmax if scale.vmax is not None else float(np.nanmax(values)),
    )
    data = {
        'x': np.asarray(cloud.x, dtype=float),
        'y': np.asarray(cloud.y, dtype=float),
        'value': values,
    }
    size = _mark_size(cloud.size)
    if isinstance(size, np.ndarray):
        # ONE SIZE PER SAMPLE IS A COLUMN, not a property: this library refuses a sequence handed to
        # a glyph that has a source ("must come from references to data columns"), so the per-sample
        # case goes into the source and is referenced by field name.
        data['size'] = size
        size = 'size'
    figure.scatter(
        'x',
        'y',
        source=ColumnDataSource(data=data),
        fill_color=transform('value', mapper),
        marker=marked(cloud.marker or 'o'),
        **given(
            size=size,
            fill_alpha=cloud.alpha,
            line_alpha=cloud.alpha,
            y_range_name=y_range_name,
        ),
    )
    figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')


def vectors(figure: Plot, arrows: Vectors, color: str, *, y_range_name: str | None) -> None:
    """Draw *arrows* — the SHAFT as one vectorized glyph, the HEAD as another.

    BOKEH HAS NO QUIVER, so this composes the two glyphs it does have rather than building one Arrow
    annotation per sample: a quiver's whole point is that a field of N arrows is one artist, and N
    annotation models would trade the primitive's cost model away for a shape it is not worth. The
    head is therefore a screen-sized marker rather than a head scaled with the arrow, which is a
    difference from the other adapter that a reader can see -- and a mark that says "here is the
    direction" is what the vocabulary promises, not a head length.

    ``|(u, v)| / scale`` is computed HERE, in data units: this library has no scaling of its own for
    a mark, so the arithmetic the vocabulary defines is the arithmetic drawn.
    """
    tip_x = np.asarray(arrows.x, dtype=float) + np.asarray(arrows.u, dtype=float) / arrows.scale
    tip_y = np.asarray(arrows.y, dtype=float) + np.asarray(arrows.v, dtype=float) / arrows.scale
    figure.segment(
        x0=arrows.x,
        y0=arrows.y,
        x1=tip_x,
        y1=tip_y,
        **given(
            line_width=arrows.width,
            line_color=color,
            line_alpha=arrows.alpha,
            y_range_name=y_range_name,
        ),
    )
    figure.scatter(
        x=tip_x,
        y=tip_y,
        marker='triangle',
        size=_VECTOR_HEAD_SIZE,
        angle=np.arctan2(np.asarray(arrows.v, dtype=float), np.asarray(arrows.u, dtype=float)) - np.pi / 2,
        **given(
            fill_color=color,
            line_color=color,
            fill_alpha=arrows.alpha,
            line_alpha=arrows.alpha,
            y_range_name=y_range_name,
        ),
    )


def colorbar(figure: Plot, bar: Colorbar) -> None:
    """Draw *bar* — a continuous bar, or one discrete band per tick when ticks are given.

    The range is REFUSED when it is open, for the reason the matplotlib adapter's own docstring
    gives: a bar over nothing has no samples to derive one from, and the alternative to the refusal
    is a legend of unknown extent drawn as if it had one.

    A DISCRETE BAR QUANTISES ITS PALETTE the same way the other adapter samples its colormap, and
    the band geometry is the PRIMITIVE's (:meth:`~lab_commons.viz.Colorbar.bands`), so the two
    libraries cannot draw two different bars for one description.
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
        figure.add_layout(ColorBar(color_mapper=mapper, title=scale.label), 'right')
        return
    edges, centers = bar.bands()
    mapper = LinearColorMapper(palette=quantized(scale.cmap, len(centers)), low=edges[0], high=edges[-1])
    drawn = ColorBar(color_mapper=mapper, title=scale.label)
    drawn.ticker = FixedTicker(ticks=list(centers))
    if bar.tick_labels is not None:
        drawn.major_label_overrides = {center: str(text) for center, text in zip(centers, bar.tick_labels, strict=True)}
    figure.add_layout(drawn, 'right')
