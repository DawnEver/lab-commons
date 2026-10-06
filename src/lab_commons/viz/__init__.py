"""Tier 2 — the family's ONE plotting vocabulary: what a figure IS, before any library draws it.

A DESCRIPTION IS DATA, A CANVAS HOLDS COORDINATE SYSTEMS, AND A RENDERER IS A CHOICE OF LIBRARY. This
module is the first two: it imports NO plotting library — not a module-scope one and not a lazy one
(`NO-LAZY-IMPORT`: a deferred import makes the graph a guess) — so a producer (a sweep, a report, a
solver's post-processing step) can declare the picture it wants on a box where no plotting library
exists at all. An adapter is then imported EXPLICITLY, and that import is what opts a consumer in::

    from lab_commons.viz.mpl import MplRenderer        # pip install 'lab-commons[viz-mpl]'
    from lab_commons.viz.bokeh import BokehRenderer    # pip install 'lab-commons[viz-bokeh]'

TWO ADAPTERS, EQUAL PEERS. Neither is the second-class spelling of the other: they share this
vocabulary, they implement the same two protocols, and the same figure description draws the same
picture through both. Which one a consumer takes is a dependency decision it makes in its own
manifest, which is why the two live in two extras rather than one.

A CANVAS IS NOT A COORDINATE SYSTEM, and separating the two is the shape of this tier rather than a
convenience:

* :class:`Figure` is the CANVAS — the page, the style, the palette cursor and the lifecycle. It
  creates frames (:meth:`Figure.frame`), writes them (:meth:`Figure.save`), shows them
  (:meth:`Figure.show`) and lets them go (:meth:`Figure.close`).
* :class:`Frame` is ONE COORDINATE SYSTEM on that canvas — its projection, its axes, its limits,
  its ticks, its title, and EVERY DRAW VERB. A figure with two panels has two frames; a figure with
  a second y scale over the first has two frames sharing an x axis; a polar figure's frame says
  ``projection='polar'``.

Collapsing those two is what made a polar figure, a twin axis and a panel grid INEXPRESSIBLE here:
there was one place to say which coordinate system a picture was in, and it was decided at
construction. A verb that cannot be honoured is an error in the ADAPTER that cannot honour it, never
a silent omission here or there.

WHY THE VOCABULARY IS TYPED RATHER THAN DUCK-TYPED. A renderer is checked against :class:`Figure`
and a frame against :class:`Frame` with ``isinstance``, so "this object is a renderer" is a question
with an answer instead of an assumption that survives until the first missing verb. Every primitive
below is a frozen dataclass: a description handed to a renderer is not a place for a producer and a
consumer to share mutable state, and a figure can then be described once and drawn, in a test, by a
renderer that records it.

WHAT A PRODUCER MAY ASSUME, stated once here because a consumer reads no other page. A canvas starts
EMPTY — frames are created by :meth:`Figure.frame` and there is no implicit one — and every frame
draws in its own coordinate system while sharing the canvas's style and palette cursor. ``save`` and
``show`` publish the whole canvas, whatever frames it holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from numpy.typing import ArrayLike

__all__ = [
    'PROJECTIONS',
    'Bars',
    'Circle',
    'Colorbar',
    'Contours',
    'Field',
    'Figure',
    'Frame',
    'Label',
    'NullFrame',
    'NullRenderer',
    'Patch',
    'Rect',
    'Scale',
    'Segment',
    'Series',
    'Style',
    'Ticks',
    'Vectors',
]

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
PROJECTIONS: Final = frozenset({'cartesian', 'polar'})

#: The ten-colour categorical cycle both adapters start from, so a figure does not change colour
#: when its backend changes. The values are the ones matplotlib's ``tab10`` and bokeh's
#: ``Category10`` already agree on, which is the whole reason to freeze them here.
_PALETTE: tuple[str, ...] = (
    '#1f77b4',
    '#ff7f0e',
    '#2ca02c',
    '#d62728',
    '#9467bd',
    '#8c564b',
    '#e377c2',
    '#7f7f7f',
    '#bcbd22',
    '#17becf',
)


@dataclass(frozen=True)
class Style:
    """The figure style, ONCE — size, typeface, type size and the categorical palette.

    THIS OBJECT IS WHY THERE IS ONE STYLE AND NOT N. Wiring a plotting library's global defaults by
    hand in several trees is how two figures from the same lab stop matching: the figure size, the
    typeface and the type size are family decisions, they are made here, and an adapter maps this
    object onto whatever its library actually has (matplotlib's process-global ``rcParams``, bokeh's
    per-figure properties).

    THE DEFAULTS ARE THE SMALLER TYPE SIZE ON PURPOSE. ``font_size`` is 10: it is the readable
    default beside an 8x6in figure, and a consumer presenting on a projector passes
    ``Style(font_size=18)`` in the one place its figures are built. Whichever number a consumer
    wanted, it now states it instead of inheriting a copy of a copy.

    Attributes:
        figure_size: inches, ``(width, height)``.
        dpi: dots per inch for raster output.
        font_family: families in preference order — the first one the box actually has is used.
        font_size: the base type size every other size is derived from.
        palette: the categorical colour cycle, index-stable so a series keeps its colour.

    """

    figure_size: tuple[float, float] = (8.0, 6.0)
    dpi: int = 100
    font_family: tuple[str, ...] = ('Times New Roman', 'STFangsong')
    font_size: float = 10.0
    palette: tuple[str, ...] = _PALETTE

    def color(self, index: int) -> str:
        """The palette colour for series *index*, wrapping — deterministic, never a shared cursor."""
        return self.palette[index % len(self.palette)]


@dataclass(frozen=True)
class Scale:
    """What a colour MEANS: the colormap, its label, and the range it is read against.

    A SCALE IS DATA BECAUSE A FIGURE IS COMPARED AGAINST ANOTHER FIGURE. Two field maps drawn from
    two runs are only comparable while the same value maps to the same colour, so the range is
    carried with the field rather than re-derived from whatever samples the second run happened to
    have. ``vmin``/``vmax`` left as ``None`` mean "derive from these values", which is the honest
    default for a single run and the wrong choice for a sweep.

    Attributes:
        cmap: a colormap name the adapter's library knows.
        label: the colour bar's text — the quantity's name, and its unit when it has one.
        vmin: the value at the bottom of the scale, or ``None`` to derive it.
        vmax: the value at the top of the scale, or ``None`` to derive it.

    """

    cmap: str = 'viridis'
    label: str | None = None
    vmin: float | None = None
    vmax: float | None = None


@dataclass(frozen=True)
class Colorbar:
    """A colour bar for a scale the FIGURE drew itself, rather than one a field drew for it.

    WHY THIS IS A PRIMITIVE AND NOT A FLAG ON A DRAWING VERB. A field map and a region map publish
    their own colour bar as part of drawing, and that is right: the bar belongs to the map. But a
    figure that colours its own glyphs — a region map assembled from per-element colours, a scatter
    whose palette was computed by hand — has no mappable to hang a bar on, and an axis with no scale
    is a picture whose colours mean nothing. This is that bar: the figure says what its colours MEAN,
    in the same vocabulary it drew them with.

    ``vmin``/``vmax`` ARE REQUIRED HERE, and the reason is the one place this primitive differs from
    a :class:`Scale` used on a field. A scale left open is derived from the samples the field carries;
    a bar over nothing has no samples, so an open range would be a bar of unknown extent that draws
    as if it had one. :meth:`Frame.draw_colorbar` refuses it instead.

    ``ticks`` GIVEN IS WHAT MAKES THE BAR DISCRETE, with one colour band centred on each tick — the
    spelling of a categorical legend, where the value between two bands does not exist (a layer
    index, a material, a phase). A bar with no ticks is continuous, and the library places its own.
    This is deliberately not a ``discrete: bool``: the ticks ARE the band edges, so a flag beside
    them could disagree with them.

    Attributes:
        scale: what the colours mean — colormap, label and the range the bar spans.
        ticks: the tick positions, or ``None`` for a continuous bar over ``scale``'s range.
        tick_labels: the text at each tick, or ``None`` to print the numbers; the two are read
            positionally, so a label list has one entry per tick.

    """

    scale: Scale
    ticks: Sequence[float] | None = None
    tick_labels: Sequence[str] | None = None

    def bands(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """``(edges, centres)`` of a discrete bar — what ``ticks`` MEANS, read in ONE place.

        THIS IS ON THE PRIMITIVE BECAUSE IT IS PART OF THE DESCRIPTION, not a rendering choice. Two
        adapters each deciding where a band starts would be two pictures of one figure the moment
        either arithmetic drifted, which is the failure this whole tier exists to prevent; here both
        call the same method, and the rule is the one the family's own region maps already use — each
        band is centred on its tick, the edges sit midway between neighbouring ticks, and the two
        outer edges are half a step beyond the outermost ones (a lone tick gets a unit-wide band).

        Raises:
            ValueError: when ``ticks`` is ``None``, because a continuous bar has no bands.

        """
        if self.ticks is None:
            msg = 'a continuous Colorbar has no bands -- ticks is None, so no band edge exists to name'
            raise ValueError(msg)
        centres = tuple(float(tick) for tick in self.ticks)
        step = (centres[-1] - centres[0]) / (len(centres) - 1) if len(centres) > 1 else 1.0
        middles = tuple((before + after) / 2.0 for before, after in pairwise(centres))
        return (centres[0] - step / 2.0, *middles, centres[-1] + step / 2.0), centres


@dataclass(frozen=True)
class Series:
    """A labelled ``x``/``y`` trace: a chart line, a waveform, or a marker cloud.

    One type covers line and markers because a producer holds one pair of arrays; which of the two
    verbs it passes them to decides how they are drawn. A waveform is several of these sharing an
    ``x`` axis, distinguished by ``label``.

    Attributes:
        x: the abscissa, one coordinate per sample.
        y: the ordinate, the same length as *x*.
        label: the legend entry; a series with no label is not legended.
        color: an explicit colour, or ``None`` to take the next palette colour.
        style: the line style — ``'-'``, ``'--'``, ``':'``, ``'-.'``.
        width: the line width in points, or ``None`` for the library's own default.
        marker: the symbol :meth:`Frame.draw_markers` draws, e.g. ``'o'``, ``'s'``, ``'^'``.
        size: the symbol size in points, or ``None`` for the library's own default.
        alpha: opacity, ``1.0`` for opaque — a trace behind another is read through it.

    """

    x: ArrayLike
    y: ArrayLike
    label: str | None = None
    color: str | None = None
    style: str = '-'
    width: float | None = None
    marker: str | None = None
    size: float | None = None
    alpha: float = 1.0


@dataclass(frozen=True)
class Bars:
    """A bar chart's bars: a position and a height per bar.

    THE WIDTH HAS A VALUE HERE RATHER THAN "LET THE RENDERER DECIDE", and it is the one field in this
    vocabulary that does. The two libraries' own defaults DISAGREE -- matplotlib draws a bar 0.8
    wide and bokeh draws one 1.0 wide -- so a None would mean a description drawn as different
    pictures by different backends, which is the one thing this layer exists to prevent. A producer
    with a bar chart whose widths carry meaning states them; one that does not gets the conventional
    gap between bars either way.

    Attributes:
        x: the position of each bar's centre.
        height: the bar's value at that position.
        width: the bar width, in the same units as *x*.
        label: the legend entry; a chart with no label is not legended.
        color: an explicit colour, or ``None`` to take the next palette colour.
        alpha: opacity, ``1.0`` for opaque.

    """

    x: ArrayLike
    height: ArrayLike
    width: float = 0.8
    label: str | None = None
    color: str | None = None
    alpha: float = 1.0


@dataclass(frozen=True)
class Patch:
    """A closed region given by its boundary — a slot's conductor, a magnet pocket, a cell.

    ``value`` is how a region map is drawn: the region is coloured by a number rather than by a
    name, through the :class:`Scale` its verb is given. ``color`` is the other half, for a figure
    where the region's identity is a category (a phase) rather than a magnitude.

    Attributes:
        vertices: the boundary, shape ``(n, 2)``, closed implicitly (do not repeat the first point).
        value: the scalar this region carries, or ``None`` when it is coloured by name.
        color: the face colour, used when *value* is ``None``; ``None`` means unfilled.
        edgecolor: the boundary colour, or ``None`` for no outline.
        label: the legend entry; a patch with no label is not legended.
        alpha: opacity, ``1.0`` for opaque — a winding layout stacks conductors and reads through.
        hatch: ONE hatch character over the face, e.g. ``'/'``, or ``None`` for a flat fill. It is
            one character and not a string because that is the whole of what both libraries carry:
            matplotlib spells density by REPEATING the character and bokeh takes it once, so a
            two-character pattern is a description the two adapters would draw differently.

    """

    vertices: ArrayLike
    value: float | None = None
    color: str | None = None
    edgecolor: str | None = None
    label: str | None = None
    alpha: float = 1.0
    hatch: str | None = None


@dataclass(frozen=True)
class Circle:
    """A circle at a point — a coil side's symbol, a shaft, a node in a connection diagram.

    Attributes:
        x: the centre's abscissa.
        y: the centre's ordinate.
        radius: the radius, in the figure's own units.
        color: the face colour, or ``None`` to leave it unfilled.
        edgecolor: the boundary colour, or ``None`` for no outline.
        label: the legend entry; a circle with no label is not legended.

    """

    x: float
    y: float
    radius: float
    color: str | None = None
    edgecolor: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class Segment:
    """A straight segment between two points, optionally headed — a wire, an axis, an arrow.

    Attributes:
        x0: the start's abscissa.
        y0: the start's ordinate.
        x1: the end's abscissa.
        y1: the end's ordinate.
        label: the legend entry; a segment with no label is not legended.
        color: an explicit colour, or ``None`` to take the next palette colour.
        width: the line width in points, or ``None`` for the library's own default.
        style: the line style — ``'-'``, ``'--'``, ``':'``, ``'-.'``.
        arrow: draw a head at the END point, which is what makes direction readable.
        alpha: opacity, ``1.0`` for opaque.

    """

    x0: float
    y0: float
    x1: float
    y1: float
    label: str | None = None
    color: str | None = None
    width: float | None = None
    style: str = '-'
    arrow: bool = False
    alpha: float = 1.0


@dataclass(frozen=True)
class Label:
    """Text at a point, with the anchor that decides what the point MEANS.

    Attributes:
        x: the abscissa the anchor is placed at.
        y: the ordinate the anchor is placed at.
        text: the text itself.
        color: the text colour, or ``None`` for the library's own default.
        size: the type size in points, or ``None`` to inherit the style's.
        halign: the horizontal anchor — ``'left'``, ``'center'`` or ``'right'``.
        valign: the vertical anchor — ``'top'``, ``'center'`` or ``'bottom'``.
        box: draw a translucent chip between the text and what is under it.

    THE ANCHORS ARE SPELLED OUT rather than left at matplotlib's ``ha``/``va``: a two-letter name is
    one unit symbol away from a quantity, and this vocabulary is data a producer reads without the
    plotting library's abbreviations in mind.

    ``box`` EXISTS BECAUSE A LABEL IS OFTEN DRAWN ON TOP OF SOMETHING. A slot id over a coloured
    conductor, a value over a filled region map: the text and the picture are the same pixels, and
    the chip is what keeps the first readable. It defaults to OFF, because a label over white paper
    needs no chip and one silently added would be a box the description never asked for.

    """

    x: float
    y: float
    text: str
    color: str | None = None
    size: float | None = None
    halign: str = 'center'
    valign: str = 'center'
    box: bool = False


@dataclass(frozen=True)
class Ticks:
    """Where one axis's ticks ARE, and what they SAY — the slot numbers, the layer names, the degrees.

    WHY A PRODUCER ASKS FOR THIS AT ALL. Left alone, an axis labels itself with round numbers, which
    is right for a physical quantity and wrong for an INDEX: a winding figure's abscissa is a slot id
    and its ordinate is a layer id, and a reader who cannot find slot 7 on the axis cannot read the
    picture. The positions are stated rather than implied because the two are independent — a slot
    figure ticks every slot and labels every one of them, a dense one ticks each slot and labels
    every other.

    ``labels`` IS READ ON PRESENCE, NOT ON TRUTH, and that distinction is the whole reason this is a
    dataclass. ``None`` means "let the library number them"; an EMPTY sequence means "these positions
    and no text at all" — which is how a figure keeps its tick marks for alignment while showing
    none of their numbers. A ``labels or None`` in an adapter would collapse the second into the
    first, so both adapters read ``is not None``.

    Attributes:
        positions: the tick positions, in the axis's own units.
        labels: one label per position, an empty sequence for no labels, or ``None`` to number them.

    """

    positions: ArrayLike
    labels: Sequence[str] | None = None


@dataclass(frozen=True)
class Field:
    """A scalar sampled at points — the field map.

    The samples are a POINT SET rather than a rectangular grid, because the meshes a field is
    computed on are not rectangular: a structured polar chart and an unstructured mesh both arrive
    here as coordinates and values, and the adapter picks the primitive its library has for that
    (contours over a triangulation, or a quad mesh) rather than making the producer reshape.

    Attributes:
        x: the sample abscissae.
        y: the sample ordinates.
        values: one value per sample, the same length as *x*.
        scale: what the colour means — colormap, label and range.

    """

    x: ArrayLike
    y: ArrayLike
    values: ArrayLike
    scale: Scale | None = None


@dataclass(frozen=True)
class Contours:
    """The ISOLINES of a scalar over a point set — a contour drawn as LINES, over something else.

    THIS IS NOT A :class:`Field` WITH A FLAG, which is why it is its own primitive. A field is a
    surface that carries a scale and publishes a colour bar; a contour is a set of lines at stated
    levels, drawn OVER a surface whose colours already mean something — a set of equipotentials
    over a flux-density map, a phase boundary over a region map. A field has no ``color`` because
    its colour IS its value; a contour does, because its lines are an annotation of a scale that
    lives elsewhere. Folding the two into one type would give every renderer a mode it must honour
    in half its calls.

    Attributes:
        x: the sample abscissae.
        y: the sample ordinates.
        values: one value per sample, the same length as *x*.
        levels: how many contour levels to draw between the smallest and largest value.
        color: the line colour, or ``None`` for the library's own cycle.
        width: the line width in points, or ``None`` for the library's own default.
        alpha: opacity, ``1.0`` for opaque — an overlay is usually drawn translucent.

    """

    x: ArrayLike
    y: ArrayLike
    values: ArrayLike
    levels: int = 10
    color: str | None = None
    width: float | None = None
    alpha: float = 1.0


@dataclass(frozen=True)
class Vectors:
    """A vector sampled at points — the quiver: one arrow per sample, its direction and length.

    WHY A VECTOR IS NOT A :class:`Segment` WITH A HEAD. They are different objects at every level.
    A segment is two points that a producer already knows; a vector is a POSITION AND A COMPONENT
    PAIR, and the arrow's length is a SCALING decision rather than a coordinate — ``scale`` is the
    one number that makes a field of arrows readable, and a segment list would bury it in N
    multiplications the producer then has to redo to change it. The same difference separates the
    libraries' primitives: a quiver is ONE artist over N samples in both, and a vectorized glyph is
    what makes drawing a field of 10 000 arrows a single call rather than 10 000 of them.

    THE LENGTH IN DATA UNITS IS ``|(u, v)| / scale``, which is matplotlib's convention and the one
    the bokeh adapter computes from. A producer may equally pass already-scaled components and leave
    ``scale`` at its default.

    Attributes:
        x: the sample abscissae.
        y: the sample ordinates.
        u: the abscissa component of each sample's vector.
        v: the ordinate component of each sample's vector.
        scale: how long ``|(u, v)|`` is drawn — the arrow is ``|(u, v)| / scale`` data units long.
        color: an explicit colour, or ``None`` to take the next palette colour.
        width: the shaft width in points, or ``None`` for the library's own default.
        alpha: opacity, ``1.0`` for opaque.

    """

    x: ArrayLike
    y: ArrayLike
    u: ArrayLike
    v: ArrayLike
    scale: float = 1.0
    color: str | None = None
    width: float | None = None
    alpha: float = 1.0


@runtime_checkable
class Frame(Protocol):
    """ONE COORDINATE SYSTEM ON A CANVAS: its projection, its axes, and every draw verb.

    THE DRAWING VERBS TAKE THE PRIMITIVES ABOVE AND NOTHING ELSE. A verb with a bag of keyword
    arguments would let a producer spell the same figure two ways and the two adapters disagree
    about which spelling they honour; a primitive is the one spelling, and an extension is a field
    on a dataclass — visible to every renderer at once, which is the property this layer exists for.

    THE AXIS VERBS AND THE DRAW VERBS ARE BOTH HERE, and the split against :class:`Figure` is not
    "drawing versus lifecycle" but "this coordinate system versus the page": a title, a label, a
    limit, a tick, an aspect, a grid and a legend all belong to ONE axes, which is why they are not
    on the canvas. A frame that is drawn OVER another one (a twin axis) has its own y axis while
    sharing the x one — the rule for that is :meth:`Figure.frame`'s.

    Attributes:
        rect: where this frame sits on the canvas, ``(left, bottom, width, height)`` in canvas
            fractions — or ``None`` when the producer left the placement to the canvas, which draws
            it as its own default panel. Two frames of one canvas may carry the same rect, which is
            what an overlaid twin axis IS.
        projection: this frame's coordinate system, one of :data:`PROJECTIONS`.

    """

    rect: Rect | None
    projection: str

    def set_title(self, text: str) -> None:
        """Set the figure's title."""

    def set_xlabel(self, text: str) -> None:
        """Set the x axis's label."""

    def set_ylabel(self, text: str) -> None:
        """Set the y axis's label."""

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Fix the axis limits; a pair given high-to-low INVERTS that axis."""

    def set_ticks(self, *, x: Ticks | None = None, y: Ticks | None = None) -> None:
        """Place the ticks of either axis at the positions a :class:`Ticks` names."""

    def set_equal_aspect(self, *, on: bool = True) -> None:
        """Draw one unit of x at the same size as one unit of y, so a shape keeps its shape."""

    def set_axis_off(self, *, off: bool = True) -> None:
        """Hide the axes, their ticks and their frame — a drawing, not a chart."""

    def grid(self, *, on: bool = True) -> None:
        """Show or hide the grid."""

    def legend(self, *, on: bool = True) -> None:
        """Show or hide the legend of everything labelled so far."""

    def draw_line(self, series: Series) -> None:
        """Draw *series* as a line."""

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols, with no connecting line."""

    def draw_bars(self, bars: Bars) -> None:
        """Draw *bars* as bars."""

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Draw filled regions; *scale* colours them by value when they carry one."""

    def draw_circles(self, circles: Sequence[Circle]) -> None:
        """Draw circles."""

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Draw straight segments, with a head where one was asked for."""

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Draw text."""

    def draw_field(self, field: Field) -> None:
        """Draw a scalar field and its colour scale — the field map."""

    def draw_contours(self, contours: Contours) -> None:
        """Draw *contours* as isolines, with no fill and no colour bar of their own."""

    def draw_vectors(self, vectors: Vectors) -> None:
        """Draw *vectors* as arrows — the quiver, one head per sample."""

    def draw_colorbar(self, bar: Colorbar) -> None:
        """Draw *bar* as a colour bar for a scale the figure itself set.

        THE RANGE MUST BE PINNED, and an adapter that cannot read a range from the bar REFUSES it
        rather than inventing one: see :class:`Colorbar`, where the alternative to a refusal is a
        legend whose extent is a guess. A bar with ticks is drawn in DISCRETE bands, one per tick.
        """


@runtime_checkable
class Figure(Protocol):
    """THE CANVAS: the page a producer builds frames on, and what gets written or shown.

    ONE PICTURE PER FIGURE, and a picture may hold SEVERAL coordinate systems. A canvas starts
    EMPTY -- :meth:`frame` creates every frame, and the absence of an implicit one is what lets a
    two-panel comparison, a polar star and a twin axis be the same kind of object. It also keeps a
    renderer cheap to pass into a function as "where the picture goes": the function creates the
    frame it draws on.

    THE STYLE AND THE PALETTE CURSOR ARE THE CANVAS'S, NOT A FRAME'S. Two frames of one canvas draw
    in one typeface and from one palette sequence, so a series on a second panel — or on a twin
    axis, where the two y scales share the same pixels — never takes the colour of the series it is
    drawn beside.
    """

    def frame(
        self,
        *,
        rect: Rect | None = None,
        projection: str | None = None,
        sharex: Frame | None = None,
        sharey: Frame | None = None,
    ) -> Frame:
        """Create a frame on this canvas and return it — the coordinate system the verbs draw on.

        ``rect`` is ``(left, bottom, width, height)`` in canvas fractions; ``None`` means THE CANVAS
        DECIDES — with one exception: a frame that names no rect and shares an X axis is drawn OVER
        the frame it shares with, at that frame's rect, which is what a twin axis is. State a rect
        to lay a frame out anywhere else (a panel below, beside, or in a grid).

        ``projection`` is one of :data:`PROJECTIONS`, or ``None`` for ``'cartesian'`` — and for the
        projection of the frame an overlaying frame is drawn over. An unknown name RAISES, naming
        the set: a coordinate system nobody implemented must not draw as if it were cartesian.

        ``sharex``/``sharey`` name another frame whose axis this one shares (one limit, one zoom,
        both directions). Three combinations are the shapes this vocabulary exists for, and the
        rest are refused by name:

        * two frames, two rects, ``sharex`` — a panel grid, where a reader compares columns;
        * the same rect and ``sharex``, or ``sharex`` and no rect — a TWIN AXIS: a second y scale
          over one panel, its axis on the right, sharing the x axis and the pixels;
        * ``sharey`` between frames at different rects — the transposed panel grid.

        A frame may share an axis only with a frame in the SAME coordinate system, a frame drawn
        over another has its own y (so ``sharey`` on a twin RAISES), and a frame that shares only a
        y axis must name its rect — placement by inheritance means "drawn over", which is an x-axis
        relationship and cannot be read off a shared y. Two frames that would coincide while
        sharing only their y — a twin whose axis is on the top — RAISE for the same reason rather
        than overlapping two y axes on one side.
        """

    def save(self, path: Path | str) -> Path | None:
        """Write the figure — EVERY frame of it — to *path* and return where it went.

        ``None`` means nothing was written. The resolution is the style's ``dpi``, for the reason
        the frame verbs give above.
        """

    @property
    def frames(self) -> tuple[Frame, ...]:
        """Every frame this canvas holds, in creation order — the canvas's own registry.

        READ-ONLY ON PURPOSE: a consumer that could append to it could corrupt the layout an adapter
        builds from it, and a frame is created by :meth:`frame` rather than inserted. It exists so
        that a producer holding a canvas (rather than each frame it made) can still reach them —
        restyling every panel of a finished figure, or handing a renderer's own frames to a second
        pass.
        """

    def show(self, *, interactive: bool = True) -> None:
        """Display the figure, degrading to nothing when this box cannot open a window."""

    def close(self) -> None:
        """Release the figure."""


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


def _as_rect(rect: Rect) -> Rect:
    """*rect* as four plain floats, refusing anything that is not four numbers."""
    if len(rect) != len(_RECT_FIELDS):
        msg = f'a rect is {", ".join(_RECT_FIELDS)} -- four numbers in canvas fractions; got {rect!r}'
        raise ValueError(msg)
    left, bottom, width, height = (float(value) for value in rect)
    return left, bottom, width, height


class NullFrame:
    """A coordinate system that accepts every verb and draws nothing — what the default hands back.

    TOTAL BY CONSTRUCTION, and that is the whole of its job: a producer declares its figure ONCE,
    unconditionally, and "does this box draw?" is answered by which canvas it built rather than by
    a branch at every drawing call. A frame that dropped a verb would move that branch back, so
    every verb of :class:`Frame` — including the ones an adapter refuses to DRAW — is here.

    THE RECT AND THE PROJECTION ARE KEPT, because they are the description rather than the drawing:
    a producer (or a test) can still read the coordinate system it asked for, and a frame with no
    library behind it is still the shape the adapters were asked for.
    """

    def __init__(self, *, rect: Rect, projection: str) -> None:
        """Carry the placement nothing is drawn on."""
        self.rect = rect
        self.projection = projection

    def set_title(self, text: str) -> None:
        """Accepted and ignored."""

    def set_xlabel(self, text: str) -> None:
        """Accepted and ignored."""

    def set_ylabel(self, text: str) -> None:
        """Accepted and ignored."""

    def set_limits(self, *, x: tuple[float, float] | None = None, y: tuple[float, float] | None = None) -> None:
        """Accepted and ignored."""

    def set_ticks(self, *, x: Ticks | None = None, y: Ticks | None = None) -> None:
        """Accepted and ignored."""

    def set_equal_aspect(self, *, on: bool = True) -> None:
        """Accepted and ignored."""

    def set_axis_off(self, *, off: bool = True) -> None:
        """Accepted and ignored."""

    def grid(self, *, on: bool = True) -> None:
        """Accepted and ignored."""

    def legend(self, *, on: bool = True) -> None:
        """Accepted and ignored."""

    def draw_line(self, series: Series) -> None:
        """Accepted and ignored."""

    def draw_markers(self, series: Series) -> None:
        """Accepted and ignored."""

    def draw_bars(self, bars: Bars) -> None:
        """Accepted and ignored."""

    def draw_patches(self, parts: Sequence[Patch], scale: Scale | None = None) -> None:
        """Accepted and ignored."""

    def draw_circles(self, circles: Sequence[Circle]) -> None:
        """Accepted and ignored."""

    def draw_segments(self, segments: Sequence[Segment]) -> None:
        """Accepted and ignored."""

    def draw_labels(self, labels: Sequence[Label]) -> None:
        """Accepted and ignored."""

    def draw_field(self, field: Field) -> None:
        """Accepted and ignored."""

    def draw_contours(self, contours: Contours) -> None:
        """Accepted and ignored."""

    def draw_vectors(self, vectors: Vectors) -> None:
        """Accepted and ignored."""

    def draw_colorbar(self, bar: Colorbar) -> None:
        """Accepted and ignored — the range a drawing adapter must be able to read is not read here.

        An adapter that DRAWS the bar refuses an unpinned range; this one has nothing to read it for,
        and raising would make a description refuse to exist rather than refuse to be drawn.
        """


class NullRenderer:
    """The DEFAULT renderer: every verb accepted, nothing drawn, no plotting library involved.

    WHY DRAWS-NOTHING IS THE DEFAULT AND NOT AN ERROR. A sweep, a batch or an unattended report runs
    on boxes where no plotting library is installed, and the alternatives to this class are both
    worse: make a plotting library a hard dependency of every consumer, or scatter ``if plot:``
    branches through every producer so that "draw me this" and "decide whether to draw" are the same
    line of code. Here a producer declares its figure ONCE, unconditionally, and the renderer decides
    whether that figure becomes a file, a window, or nothing at all — the decision lives at the one
    place that built the renderer.

    IT RESOLVES FRAMES BY THE SAME RULE THE ADAPTERS DO. :func:`_place` is called here too, so a
    description that would raise against matplotlib raises identically against nothing: a batch that
    runs unattended on a box with no plotting library is exactly where a figure that cannot be drawn
    must still be caught, and "it only fails where it is drawn" would be a surprise saved for the
    one machine nobody is watching.

    It is also the renderer a TEST passes: a function that takes a renderer can be driven with this
    one and its drawing calls are then an executed, assertion-free part of the covered code path
    rather than a branch nobody runs.

    EXAMPLE (the whole of it — a producer needs no other import)::

        renderer = NullRenderer()
        renderer.frame().set_title('no window, no file, no library')
    """

    def __init__(self) -> None:
        """Build an empty canvas — the frames a producer creates are the only thing it holds."""
        self._frames: list[NullFrame] = []

    def frame(
        self,
        *,
        rect: Rect | None = None,
        projection: str | None = None,
        sharex: Frame | None = None,
        sharey: Frame | None = None,
    ) -> NullFrame:
        """Create a frame that accepts every verb and draws nothing, and return it.

        The placement is resolved by the shared rule (:func:`_place`), so the rect a twin inherits,
        the projection a frame is built in and every refusal are the same ones the drawing adapters
        apply — a figure declared on a box with no plotting library is the figure that would have
        been drawn.
        """
        placement = _place(rect=rect, projection=projection, sharex=sharex, sharey=sharey)
        frame = NullFrame(rect=placement.rect, projection=placement.projection)
        self._frames.append(frame)
        return frame

    @property
    def frames(self) -> tuple[NullFrame, ...]:
        """Every frame this canvas holds, in creation order."""
        return tuple(self._frames)

    def save(self, path: Path | str) -> Path | None:
        """Write nothing, and answer ``None`` rather than the path it was handed.

        A caller may branch on the return value to mean "the file is there", so answering the path
        would be the declaration-that-lies shape in miniature -- one boolean's worth of it.
        """

    def show(self, *, interactive: bool = True) -> None:
        """Accepted and ignored — there is no window without a plotting library, and no pretence."""

    def close(self) -> None:
        """Accepted and ignored, because nothing was opened."""
