"""The matplotlib adapter — importing THIS module is what opts a consumer in.

This is the second half of :mod:`lab_commons.viz`. The vocabulary module imports no plotting library
at all; this one imports matplotlib at module scope, so the import itself is the opt-in, and it is
paid for by the ``viz-mpl`` extra. ``NO-LAZY-IMPORT`` is why nothing here defers that import into a
function: a deferred import makes the import graph a guess for every reader and every cost model,
and the honest spelling of "optional" is an OPTIONAL MODULE a consumer names explicitly.

WHAT LIVES HERE BESIDE THE RENDERER, and why it is this module rather than the vocabulary one:
:func:`enable_interactive_backend`, :func:`is_interactive_backend` and :func:`show_or_save` are the
family's whole display policy -- the ONE place that knows GUI toolkits exist, whether a window may
open, and whether a figure is a file, a window or both. They are here because they are matplotlib's
answers: bokeh has no dead-canvas failure mode to guard against (its ``show`` opens a browser and its
output is HTML), and a policy shared with a library that cannot fail that way would be a policy
nobody could check.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PatchCollection
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from matplotlib.patches import Circle as CirclePatch
from matplotlib.patches import Polygon as PolygonPatch

from lab_commons.viz import Bars, Circle, Field, Label, Patch, Scale, Segment, Series, Style

__all__ = [
    'MplRenderer',
    'apply_style',
    'enable_interactive_backend',
    'is_interactive_backend',
    'show_or_save',
]

#: The backends that CANNOT open a window. Lower-case, because ``mpl.get_backend()`` casing varies
#: while matplotlib normalises its backend names case-insensitively.
_NONINTERACTIVE_BACKENDS: Final = frozenset({'agg', 'pdf', 'ps', 'svg', 'cairo', 'template'})


def apply_style(style: Style | None = None) -> None:
    """Apply *style* to matplotlib's ``rcParams`` — the ONE place the family's figure style is set.

    THIS FUNCTION IS THE SINGLE SOURCE OF TRUTH FOR FIGURE TYPOGRAPHY, and it earns that name by
    replacing a shape that had already drifted: several trees wiring ``rcParams`` by hand, with the
    same keys and different values, so two figures from the same family stopped matching and no
    disagreement was visible in any one of them. A style is a typed object now; restyling is
    ``Style(font_size=18)`` rather than a free-form dictionary, and a key that is not a real
    ``rcParam`` is a dataclass field that does not exist rather than a ``KeyError`` at plot time.

    ``rcParams`` IS PROCESS-GLOBAL AND THIS DOES NOT PRETEND OTHERWISE. matplotlib resolves a text
    element's family and size from the global table when the figure is DRAWN, so there is no
    per-figure typography to set and the last style applied wins for everything drawn afterwards.
    That is the library's semantics; the repair is to apply one style in one place, which is this
    function, rather than to subtract from the library.
    """
    chosen = Style() if style is None else style
    mpl.rcParams.update(
        {
            'axes.unicode_minus': True,
            'figure.constrained_layout.use': True,
            'figure.figsize': chosen.figure_size,
            'figure.dpi': chosen.dpi,
            'font.family': list(chosen.font_family),
            'font.size': chosen.font_size,
            'axes.titlesize': chosen.font_size,
            'figure.titlesize': chosen.font_size,
            'legend.fontsize': chosen.font_size,
        }
    )


def enable_interactive_backend() -> bool:
    """Switch matplotlib to an available INTERACTIVE (GUI) backend for on-screen plots.

    Tries the common GUI backends in order and returns ``True`` as soon as one activates, so the
    caller may call ``plt.show()``. When NONE is available -- a headless / CI / bare-Windows box with
    no Qt or Tk -- it returns ``False`` without forcing anything, so the caller can honestly degrade
    to SAVING figures instead of calling ``plt.show()`` on the Agg canvas, which only emits the
    confusing "FigureCanvasAgg is non-interactive, and thus cannot be shown" warning and displays
    nothing. ``switch_backend`` raises when a backend's GUI toolkit is not importable, so a returned
    ``True`` means the backend really loaded, not just that its name was set.

    A NON-INTERACTIVE ``MPLBACKEND`` DECLARED IN THE ENVIRONMENT WINS: it is the process owner's
    statement that no window may open (a test harness sets ``Agg``), and a plotting request does not
    override it. MEASURED 2026-10-05: a light test run opened a live TkAgg window on the user's
    desktop because this function ignored that declaration.
    """
    declared = os.environ.get('MPLBACKEND', '').strip().lower()
    if declared in _NONINTERACTIVE_BACKENDS:
        return False
    for backend in ('QtAgg', 'Qt5Agg', 'TkAgg', 'MacOSX', 'GTK4Agg', 'wxAgg'):
        try:
            plt.switch_backend(backend)
        except Exception:  # noqa: BLE001, S112 -- a missing toolkit OR a display that cannot open is "try the next"
            continue
        if mpl.get_backend().lower() not in _NONINTERACTIVE_BACKENDS:
            return True
    return False


def is_interactive_backend() -> bool:
    """True when the LIVE matplotlib backend is a GUI (showable) backend right now."""
    return mpl.get_backend().lower() not in _NONINTERACTIVE_BACKENDS


def show_or_save(
    fig: Figure,
    out_path: Path | str | None = None,
    *,
    interactive: bool = False,
    dpi: int = 150,
) -> Path | None:
    """Save a figure, show it, or both — and never ``plt.show()`` on a dead canvas.

    ``plt.show()`` runs ONLY when the caller asked for interactive display AND the live backend is
    actually a GUI backend at THIS moment (any code path may have switched the process-global backend
    to Agg in between -- a headless box, a prior save-only figure). Otherwise the run degrades to
    files instead of emitting the confusing "FigureCanvasAgg is non-interactive, and thus cannot be
    shown" warning and showing nothing. Every renderer routes its show/save tail through here so no
    plotting site can reintroduce the bug.

    SAVING AND SHOWING ARE NOT ALTERNATIVES, and the name's ``or`` is historical. A version of this
    function returned from the show branch before it could ever write the file, so asking for a
    window DISCARDED the output -- measured on a run whose every same-config sibling without the
    interactive flag wrote its PNG and which wrote none. In a workflow where a figure IS an output,
    that is the code disagreeing with the workflow's own stated rule, and the code was wrong.

    THE SAVE COMES FIRST, which is the half that is not merely tidiness. ``plt.show()`` BLOCKS until
    the window is closed, so a figure saved afterwards is one a Ctrl-C at the window never gets.
    Written first, the file is on disk before the caller is handed a window they may kill.

    Returns the saved path when a file was written -- INCLUDING when a window was also shown -- and
    ``None`` when there was nothing to write. A show that was asked for and could not happen is not
    reported here: this function returns where the figure went, not whether a window appeared, and
    the Agg fallback above is the documented degrade.
    """
    out = None
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, bbox_inches='tight')

    if interactive and is_interactive_backend():
        # `plt.show()` blocks and then the window owns the figure's lifetime, so this branch does
        # NOT close it -- closing a shown figure is what produced the "window flashes and vanishes"
        # shape on backends that return from `show()` immediately.
        plt.show()
        return out

    plt.close(fig)
    return out


class MplRenderer:
    """A matplotlib figure, drawn through the family vocabulary.

    Attributes:
        style: the :class:`~lab_commons.viz.Style` this figure was built with.
        figure: the matplotlib ``Figure`` itself -- the adapter's own escape hatch. A consumer that
            imported this module has matplotlib already, so a figure that needs one call the
            vocabulary does not carry yet is reachable without rebuilding it by hand.

    """

    def __init__(self, style: Style | None = None) -> None:
        """Build one empty figure: apply the style, then create the axes it is drawn on.

        THE STYLE IS APPLIED HERE because matplotlib resolves type from the process-global
        ``rcParams`` at draw time -- see :func:`apply_style` for the consequence, which is stated
        rather than worked around.
        """
        self.style = Style() if style is None else style
        apply_style(self.style)
        self.figure = plt.figure(figsize=self.style.figure_size, dpi=self.style.dpi)
        self._axes = self.figure.add_subplot(111)
        self._drawn = 0

    def set_title(self, text: str) -> None:
        """Set the figure's title."""
        self._axes.set_title(text)

    def set_xlabel(self, text: str) -> None:
        """Set the x axis's label."""
        self._axes.set_xlabel(text)

    def set_ylabel(self, text: str) -> None:
        """Set the y axis's label."""
        self._axes.set_ylabel(text)

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Fix the axis limits; a pair given high-to-low inverts that axis, which matplotlib honours."""
        if x is not None:
            self._axes.set_xlim(x)
        if y is not None:
            self._axes.set_ylim(y)

    def set_equal_aspect(self, *, on: bool = True) -> None:
        """Draw one unit of x at the same size as one unit of y, so a shape keeps its shape."""
        self._axes.set_aspect('equal' if on else 'auto')

    def set_axis_off(self, *, off: bool = True) -> None:
        """Hide the axes, their ticks and their frame — a drawing, not a chart."""
        if off:
            self._axes.set_axis_off()
        else:
            self._axes.set_axis_on()

    def grid(self, *, on: bool = True) -> None:
        """Show or hide the grid."""
        self._axes.grid(on)

    def legend(self, *, on: bool = True) -> None:
        """Show the legend of everything labelled so far, or remove it."""
        existing = self._axes.get_legend()
        if on:
            self._axes.legend()
        elif existing is not None:
            existing.remove()

    def draw_line(self, series: Series) -> None:
        """Draw *series* as a line, taking the next palette colour when it names none."""
        self._axes.plot(
            series.x,
            series.y,
            linestyle=series.style,
            linewidth=series.width,
            color=self._next_color(series.color),
            label=series.label,
        )

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols, with no connecting line."""
        self._axes.plot(
            series.x,
            series.y,
            linestyle='none',
            marker=series.marker or 'o',
            markersize=series.size,
            color=self._next_color(series.color),
            label=series.label,
        )

    def draw_bars(self, bars: Bars) -> None:
        """Draw *bars* as bars."""
        self._axes.bar(
            bars.x,
            bars.height,
            width=bars.width,
            color=self._next_color(bars.color),
            label=bars.label,
        )

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when the patches carry one.

        VALUE-COLOURED AND NAME-COLOURED ARE DIFFERENT FIGURES, not two spellings of one: a region
        carrying a ``value`` gets a colormap and a colour bar (the region map), and a region carrying
        a ``color`` gets that colour and a legend entry (the identity map).
        """
        polygons = [PolygonPatch(np.asarray(part.vertices, dtype=float), closed=True) for part in parts]
        values = [part.value for part in parts]
        if scale is not None and any(value is not None for value in values):
            collection = PatchCollection(polygons, cmap=scale.cmap, norm=Normalize(vmin=scale.vmin, vmax=scale.vmax))
            collection.set_array(np.asarray([np.nan if value is None else value for value in values], dtype=float))
            self._axes.add_collection(collection)
            self._axes.autoscale_view()
            self.figure.colorbar(collection, ax=self._axes, label=scale.label)
            return
        for polygon, part in zip(polygons, parts, strict=True):
            polygon.set_facecolor(part.color if part.color is not None else 'none')
            polygon.set_edgecolor(part.edgecolor if part.edgecolor is not None else 'none')
            if part.label is not None:
                polygon.set_label(part.label)
            self._axes.add_patch(polygon)

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
            self._axes.add_patch(artist)

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Draw straight segments, with a head at the end point where one was asked for."""
        for segment in segments:
            color = self._next_color(segment.color)
            if segment.arrow:
                self._axes.annotate(
                    '',
                    xy=(segment.x1, segment.y1),
                    xytext=(segment.x0, segment.y0),
                    arrowprops={
                        'arrowstyle': '-|>',
                        'color': color,
                        'linewidth': segment.width,
                        'linestyle': segment.style,
                    },
                    label=segment.label,
                )
            else:
                self._axes.plot(
                    (segment.x0, segment.x1),
                    (segment.y0, segment.y1),
                    linestyle=segment.style,
                    linewidth=segment.width,
                    color=color,
                    label=segment.label,
                )

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Draw text at its anchor."""
        for label in labels:
            self._axes.text(
                label.x,
                label.y,
                label.text,
                color=label.color,
                fontsize=label.size,
                ha=label.halign,
                va=label.valign,
            )

    def draw_field(self, field: Field) -> None:
        """Draw a scalar field and its colour scale — the field map.

        The samples go to a FILLED CONTOUR OVER A TRIANGULATION, which is the primitive that accepts
        a point set with no structure: a structured chart, a skewed one and an unstructured mesh all
        arrive here as coordinates and values, and nothing has to be reshaped to a rectangle the
        samples never had.
        """
        scale = Scale() if field.scale is None else field.scale
        artist = self._axes.tricontourf(
            np.asarray(field.x, dtype=float),
            np.asarray(field.y, dtype=float),
            np.asarray(field.values, dtype=float),
            cmap=scale.cmap,
            vmin=scale.vmin,
            vmax=scale.vmax,
        )
        self.figure.colorbar(artist, ax=self._axes, label=scale.label)

    def save(self, path: Path | str) -> Path | None:
        """Write the figure — and never open a window, which is the batch-run tail.

        Routed through :func:`show_or_save` so there is ONE place the display invariant lives: this
        call is ``interactive=False`` by construction, so a batch that saves cannot pop a window on
        an unattended box, and the resolution is the style's (see the protocol).
        """
        return show_or_save(self.figure, path, dpi=self.style.dpi)

    def show(self, *, interactive: bool = True) -> None:
        """Display the figure, degrading to nothing when this box cannot open a window."""
        show_or_save(self.figure, None, interactive=interactive)

    def close(self) -> None:
        """Release the figure."""
        plt.close(self.figure)

    def _next_color(self, explicit: str | None) -> str:
        """*explicit* when it is given, else the next palette entry — one slot per drawn artist.

        The counter advances whether or not the caller named a colour, so the n-th artist's palette
        position does not depend on which of its predecessors happened to name one: the same figure
        drawn twice takes the same colours.
        """
        color = explicit if explicit is not None else self.style.color(self._drawn)
        self._drawn += 1
        return color
