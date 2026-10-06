"""The matplotlib adapter — importing THIS module is what opts a consumer in.

This is the canvas half of :mod:`lab_commons.viz`: the matplotlib ``Figure``, the style applied to
it, the palette cursor its frames share, the lifecycle verbs -- and :meth:`MplRenderer.frame`, which
creates the :class:`~lab_commons.viz.mpl_frame.MplFrame` that every draw verb belongs to. The
vocabulary module imports no plotting library at all; this one imports matplotlib at module scope, so
the import itself is the opt-in, and it is paid for by the ``viz-mpl`` extra. ``NO-LAZY-IMPORT`` is
why nothing here defers that import into a function: a deferred import makes the import graph a guess
for every reader and every cost model, and the honest spelling of "optional" is an OPTIONAL MODULE a
consumer names explicitly.

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
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Final

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from lab_commons.viz import Frame, Rect, Style, _place
from lab_commons.viz.mpl_frame import MplFrame

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

#: This library's own name for each coordinate system the vocabulary declares. ONE row per name in
#: :data:`~lab_commons.viz.PROJECTIONS`, and the lookup is what turns an unknown name into a
#: ``KeyError`` here rather than a figure drawn in the wrong system.
#:
#: ``cartesian`` IS ``None`` because matplotlib's default axes ARE the rectilinear system and its
#: projection registry has no entry for them: handing a name to ``add_axes`` that the registry
#: cannot resolve is an error, so the absence of a name is the spelling.
_MPL_PROJECTIONS: Final[dict[str, str | None]] = {'cartesian': None, 'polar': 'polar'}


def _opened(projection: str, sharex: Frame | None, sharey: Frame | None) -> dict[str, object]:
    """The keyword arguments matplotlib opens an axes with: its projection and the two shares.

    THE SHARES ARE TRANSLATED, NOT PASSED THROUGH. A shared frame's axis is an ``Axes`` of THIS
    adapter, so a frame from the other one has none to hand over -- refused by NAME here rather than
    by an ``AttributeError`` three frames later, because mixing the two adapters in one figure is a
    mistake worth reading about rather than a stack trace to decode.
    """
    for shared in (sharex, sharey):
        if shared is not None and not isinstance(shared, MplFrame):
            msg = f'a frame can share an axis only with a frame of its own adapter, not {type(shared).__name__}'
            raise TypeError(msg)
    return {
        'projection': _MPL_PROJECTIONS[projection],
        'sharex': None if sharex is None else sharex.axes,
        'sharey': None if sharey is None else sharey.axes,
    }


def _twin_of(base: Frame) -> Axes:
    """The axes of a twin frame -- matplotlib's own ``twinx`` of *base*, or a refusal by name."""
    if not isinstance(base, MplFrame):
        msg = f'a frame can be drawn over only a frame of its own adapter, not {type(base).__name__}'
        raise TypeError(msg)
    return base.axes.twinx()


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
    """A matplotlib figure — THE CANVAS, and the frames a producer creates on it.

    Attributes:
        style: the :class:`~lab_commons.viz.Style` this canvas was built with.
        figure: the matplotlib ``Figure`` itself -- the adapter's own escape hatch. A consumer that
            imported this module has matplotlib already, so a figure-level call the vocabulary does
            not carry (a figure legend over two twin axes, a ``suptitle``) is reachable without
            rebuilding anything by hand.

    """

    def __init__(self, style: Style | None = None) -> None:
        """Build one empty canvas: apply the style, then create the matplotlib figure it draws on.

        THE STYLE IS APPLIED HERE because matplotlib resolves type from the process-global
        ``rcParams`` at draw time -- see :func:`apply_style` for the consequence, which is stated
        rather than worked around.

        THE LAYOUT ENGINE IS TURNED OFF FOR THIS FIGURE, and that is a decision about what a frame
        IS rather than a style override. A figural layout engine places axes nobody placed; every
        frame of this canvas carries its own geometry, so the engine has nothing to decide -- and
        matplotlib says so on every draw of a figure whose axes were all added by hand ("there are
        no gridspecs with layoutgrids"), which a batch would then print for every figure it writes.
        A consumer that wants the engine back for this figure asks for it by name::

            renderer.figure.set_layout_engine('constrained')   # its own call, its own consequence

        NO FRAME IS CREATED HERE, and that is the difference between a canvas and a picture: a
        producer that wants a two-panel figure creates both of its frames, and one that wants a
        single panel creates the one -- an implicit default axes would be a third panel nobody asked
        for, exactly on the figures that grew past one.
        """
        self.style = Style() if style is None else style
        apply_style(self.style)
        self.figure = plt.figure(figsize=self.style.figure_size, dpi=self.style.dpi)
        self.figure.set_layout_engine('none')
        self._drawn: Iterator[int] = count()
        self._frames: list[MplFrame] = []

    def frame(
        self,
        *,
        rect: Rect | None = None,
        projection: str | None = None,
        sharex: Frame | None = None,
        sharey: Frame | None = None,
    ) -> MplFrame:
        """Create a coordinate system on this canvas — see :meth:`lab_commons.viz.Figure.frame`.

        THE PLACEMENT IS NOT DECIDED HERE. Which rect a frame occupies, which coordinate system it
        is built in and whether it is a twin axis are the vocabulary's rules, resolved once in
        :func:`lab_commons.viz._place` so both adapters apply the same ones; this method only
        translates the answer into matplotlib objects.

        A STATED RECT IS THE AXES' BOX, exactly, and a frame that named none gets the canvas's own
        panel — matplotlib's subplot geometry, with the room a normal plot leaves for its own labels
        and title around it. Those are the two statements a producer can make, and they are drawn as
        the two different things they are rather than as one rectangle with a default.

        A TWIN IS BUILT BY ``Axes.twinx()`` RATHER THAN BY A SECOND ``add_axes`` AT THE SAME RECT.
        matplotlib's own twin carries the whole treatment -- the y axis on the right, an invisible x
        axis, a transparent patch, the base's y ticks moved left, and a joined pair whose position
        follows its base's. Assembling that by hand, or leaving two independent axes to coincide
        today and drift apart the moment either moved, is what this call is for.
        """
        placement = _place(rect=rect, projection=projection, sharex=sharex, sharey=sharey)
        if placement.twin_of is not None:
            axes = _twin_of(placement.twin_of)
        elif placement.rect is None:
            axes = self.figure.add_subplot(**_opened(placement.projection, sharex, sharey))
        else:
            axes = self.figure.add_axes(placement.rect, **_opened(placement.projection, sharex, sharey))
        frame = MplFrame(
            axes=axes,
            rect=placement.rect,
            projection=placement.projection,
            style=self.style,
            drawn=self._drawn,
        )
        self._frames.append(frame)
        return frame

    @property
    def frames(self) -> tuple[MplFrame, ...]:
        """Every frame this canvas holds, in creation order — the escape hatch for a kept-nowhere one."""
        return tuple(self._frames)

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
