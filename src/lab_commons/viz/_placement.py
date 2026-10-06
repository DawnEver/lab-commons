"""WHERE A FRAME SITS on its canvas — the one placement rule, and the panel arithmetic.

WHY THIS IS NOT PART OF THE VOCABULARY MODULE. ``lab_commons.viz`` is what a picture IS: the
primitives and the two protocols a renderer answers to. This is the ARITHMETIC AND THE RESOLUTION
those protocols refer to — the ``rect`` convention, the coordinate systems a frame may be built in,
the rule that turns a ``frame(...)`` request into a placement, and the panel-grid arithmetic a
producer reads instead of hand-computing rects. The split is the module-size band's, made at the
tier's own seam rather than at a line: BOTH ADAPTERS AND :class:`~lab_commons.viz.NullRenderer` READ
these rules, and none of them implements one.

EVERY RULE HERE IS STATED ONCE, and that is the whole reason the module exists. A rule implemented
in each adapter is a rule that holds until one of them is edited, and the claim of this tier is that
one description draws one picture whichever backend is behind it — so "which rect, which coordinate
system, is this a twin" is resolved HERE, over the protocols' own data, and an adapter only
translates the answer into its library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # a type-only import: the vocabulary imports THIS module, and a cycle is not a type
    from lab_commons.viz import Frame

#: Where a frame sits on its canvas: ``(left, bottom, width, height)``, each in canvas fractions —
#: the convention matplotlib's ``add_axes`` takes and the one this vocabulary states, so a producer
#: computing a panel grid computes the same numbers for either adapter. A frame that names none is
#: left to the canvas, and its ``rect`` is then ``None`` rather than an invented rectangle.
type Rect = tuple[float, float, float, float]

#: The rect a frame covers when it names none and shares no axis: the whole canvas. It is the answer
#: an adapter uses for LAYOUT questions (which grid cell is this frame in), never a position this
#: vocabulary claims a library must draw an axes at.
_CANVAS_RECT: Final[Rect] = (0.0, 0.0, 1.0, 1.0)

#: The four numbers a rect is, BY NAME — so the arity check in :func:`_as_rect` is a comparison of
#: names rather than a literal, and the refusal it raises can say which numbers it wanted.
_RECT_FIELDS: Final = ('left', 'bottom', 'width', 'height')

#: The coordinate systems a frame may be built in, BY NAME — the ONE place the set is declared, so a
#: misspelling raises in every adapter instead of drawing something nobody asked for. Both adapters
#: read THIS set: adding a system is one line here plus its implementation in an adapter, never a
#: private second list.
#:
#: ``cartesian`` is the default and the only system the bokeh adapter can draw; ``polar`` is
#: matplotlib's polar projection (radius against angle), which bokeh has no counterpart for and
#: refuses BY NAME.
#:
#: A SYSTEM WITH A THIRD AXIS IS NOT A THIRD MEMBER, and the exclusion is the shape of the tier
#: rather than an omission: a 3D frame has verbs this one does not (a camera, a box aspect, a third
#: axis label) and LACKS verbs it has (bars, circles, a field, a quiver, ticks), so its protocol is
#: :class:`~lab_commons.viz.Frame3D` and it is asked for by NAME. Every member here is a projection
#: a ``frame``'s ``projection`` attribute can actually hold.
PROJECTIONS: Final = frozenset({'cartesian', 'polar'})

#: The name of the coordinate system a :class:`~lab_commons.viz.Frame3D` draws in — NOT a member of
#: :data:`PROJECTIONS` (see above) and not a second place a coordinate system is declared either: it
#: is the name ``frame(projection=...)`` REFUSES, with the canvas verb that does build one as the
#: remedy, so a producer who spells the system here is routed instead of told it does not exist.
_THREE_D: Final = '3d'


@dataclass(frozen=True)
class _Placement:
    """A resolved :meth:`Figure.frame` request: where the frame goes, what it draws in, what it is.

    ``rect`` is ``None`` when the producer left the placement to the canvas. ``twin_of`` is the
    frame this one is drawn OVER — a second y scale sharing the first frame's x axis and pixels —
    or ``None`` for a frame that stands on its own. It is the frame rather than a flag so that an
    adapter needs no second lookup to find the axes or the range it must attach to.
    """

    rect: Rect | None
    projection: str
    twin_of: Frame | None


def _covers(rect: Rect | None) -> Rect:
    """The territory a frame covers: *rect*, or the whole canvas when it named none.

    THE LAYOUT QUESTION, asked once. An adapter decides which grid cell or panel a frame belongs to
    from this — never from ``rect`` directly, which is ``None`` for the frame that asked the canvas
    to place it.
    """
    return _CANVAS_RECT if rect is None else rect


def _place(*, rect: Rect | None, projection: str | None, sharex: Frame | None, sharey: Frame | None) -> _Placement:
    """Resolve a frame request — THE RULE EVERY ADAPTER READS, stated once so none of them drifts.

    A rule implemented in each adapter is a rule that holds until one of them is edited, and the
    whole claim of this tier is that one description draws one picture through either backend. So
    the resolution of "which rect, which coordinate system, is this a twin" lives HERE, over the
    protocol's own data, and an adapter only translates the answer into its library.

    Raises:
        ValueError: an unknown projection name, a rect that is not four numbers, an axis shared with
            a frame in another coordinate system, a frame sharing only a y axis and naming no rect,
            or a request for something that cannot be drawn (a y-axis twin). The messages name what
            to do instead.

    """
    if projection == _THREE_D:
        msg = (
            'a 3D coordinate system is not a Frame: it has verbs a plane frame does not (a view, a box '
            'aspect, a third axis label) and lacks verbs it has, so the two are two protocols. Ask the '
            'canvas for one by name: figure.frame_3d()'
        )
        raise ValueError(msg)
    if projection is not None and projection not in PROJECTIONS:
        msg = f'unknown projection {projection!r}: a frame is one of {sorted(PROJECTIONS)}'
        raise ValueError(msg)
    stated = None if rect is None else _as_rect(rect)
    shared = sharex if sharex is not None else sharey
    if shared is None:
        return _Placement(stated, 'cartesian' if projection is None else projection, None)
    if projection is not None and projection != shared.projection:
        msg = (
            f'a frame may share an axis only with a frame in its own coordinate system: {projection!r} '
            f"against the shared frame's {shared.projection!r}"
        )
        raise ValueError(msg)
    if sharey is not None and sharex is None and stated is None:
        msg = (
            'a frame that shares only a y axis must name its rect: naming none is how a frame is drawn '
            'OVER the frame it shares an X axis with, and there is no such frame here'
        )
        raise ValueError(msg)
    if sharey is not None and stated is not None and _covers(stated) == _covers(sharey.rect):
        msg = (
            "two frames drawn over one another share an X axis, with the second one's y on the right; a "
            'twin whose y is shared and whose x is independent is not expressible'
        )
        raise ValueError(msg)
    # NAMING NO RECT WHILE SHARING AN X IS HOW A TWIN IS SPELLED, and so is repeating the rect of the
    # frame it shares that axis with: both mean "drawn over it", which is why the twin's rect is that
    # frame's and not the canvas.
    twin_of = sharex if sharex is not None and (stated is None or _covers(stated) == _covers(sharex.rect)) else None
    if twin_of is not None:
        if sharey is not None:
            msg = "a frame drawn over another is a twin axis: it shares that frame's x and has its own y"
            raise ValueError(msg)
        return _Placement(twin_of.rect, shared.projection, twin_of)
    return _Placement(stated, shared.projection, None)


def _place_3d(*, rect: Rect | None) -> _Placement:
    """Resolve a ``frame_3d`` request — a rect, and the one coordinate system a 3D frame draws in.

    A 3D frame is PLACED like any other (a stated rect, or the canvas's own panel) and takes nothing
    else: a shared axis is a relationship between two PLANE coordinate systems and a twin is a second
    AXIS on one of them, so neither has a meaning on a frame that has a third. This resolution is
    small on purpose, and it lives here rather than in each adapter for the reason the module exists.
    """
    return _Placement(None if rect is None else _as_rect(rect), _THREE_D, None)


def _as_rect(rect: Rect) -> Rect:
    """*rect* as four plain floats, refusing anything that is not four numbers."""
    if len(rect) != len(_RECT_FIELDS):
        msg = f'a rect is {", ".join(_RECT_FIELDS)} -- four numbers in canvas fractions; got {rect!r}'
        raise ValueError(msg)
    left, bottom, width, height = (float(value) for value in rect)
    return left, bottom, width, height


def panel_rects(
    nrows: int,
    ncols: int,
    *,
    left: float = 0.08,
    bottom: float = 0.09,
    right: float = 0.97,
    top: float = 0.94,
    hgap: float = 0.07,
    vgap: float = 0.12,
) -> tuple[Rect, ...]:
    """The rects of an *nrows* x *ncols* panel grid, in READING ORDER — top row first, left to right.

    THE GAPS ARE THE WHOLE REASON THIS FUNCTION EXISTS. A rect is a frame's BOX, and everything a
    reader sees around that box — the ticks, the axis labels, the title — is drawn OUTSIDE it. A grid
    whose panels touch therefore draws the upper row's x tick labels through the lower row's title,
    and a column whose boxes touch overprints the right panel's y labels with the left panel's
    drawing. Every producer laying out N panels would otherwise compute the same six numbers by hand
    and get the same six subtly wrong; this is that arithmetic, ONCE, in the vocabulary both adapters
    read — so the frames of a grid are placed identically whichever library draws them.

    ``vgap`` IS LARGER THAN ``hgap`` BY DEFAULT, and the asymmetry is the measured one rather than a
    preference: a row gap carries the upper panel's x tick labels AND its x label AND the lower
    panel's title, while a column gap carries only the right panel's y tick labels and its y label,
    which sit on ITS left edge, inside the gap.

    THE DEFAULTS ARE CANVAS FRACTIONS, stated for the family's own figure — an 8x6in canvas at
    ``Style``'s type size — and overridable one at a time. Nothing here reads a figure, a dpi or a
    type size: the arithmetic is the same for either adapter, and for a canvas that is never drawn.

    The panels are EQUAL boxes, the margins are the room the whole grid leaves around itself, and the
    bottoms of the result are distinct and decreasing — so a layout that reads rects as a grid (which
    is this library's shape on the bokeh side) sees exactly the rows and columns this call means.

    Args:
        nrows: how many rows of panels, at least one.
        ncols: how many columns of panels, at least one.
        left: the left edge of the grid, in canvas fractions.
        bottom: the bottom edge of the grid.
        right: the right edge of the grid.
        top: the top edge of the grid.
        hgap: the gap between two columns.
        vgap: the gap between two rows — the larger one, see above.

    Returns:
        One :data:`Rect` per panel, ``nrows * ncols`` of them, read like a page.

    Raises:
        ValueError: a grid with no panel in it, or margins and gaps that leave the panels no area.

    """
    if nrows < 1 or ncols < 1:
        msg = f'a panel grid is at least one row and one column; got {nrows} x {ncols}'
        raise ValueError(msg)
    width = (right - left - (ncols - 1) * hgap) / ncols
    height = (top - bottom - (nrows - 1) * vgap) / nrows
    if width <= 0.0 or height <= 0.0:
        msg = (
            f'{nrows} x {ncols} panels do not fit between the margins and the gaps asked for: they leave a '
            f'{width:.3f} x {height:.3f} panel, and a panel with no area is not a picture. Widen the grid '
            f'(left/bottom/right/top) or narrow the gaps (hgap/vgap)'
        )
        raise ValueError(msg)
    return tuple(
        (left + col * (width + hgap), top - height - row * (height + vgap), width, height)
        for row in range(nrows)
        for col in range(ncols)
    )
