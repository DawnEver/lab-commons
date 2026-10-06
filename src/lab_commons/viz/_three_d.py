"""THE 3D PROTOCOL — a third coordinate system, and the one shape only it draws.

WHY 3D IS NOT A THIRD ``projection`` ON :class:`~lab_commons.viz.Frame`. A protocol is a promise about
verbs, and the two arities make OPPOSITE promises: a 3D frame HAS verbs a plane frame does not (a
camera, a box aspect, a third axis label) and LACKS verbs it has (bars, circles, a raster grid, a
field, a quiver, ticks). Folding them into one type would leave half of every verb table refusing per
call, and ``isinstance(frame, Frame)`` — which this tier says is "a question with an answer instead of
an assumption" — would answer "of some arity". So the two are TWO protocols, a 3D frame is asked for
by NAME (:meth:`~lab_commons.viz.Figure.frame_3d`), and ``frame(projection='3d')`` is REFUSED with
that name as its remedy.

WHAT IS REUSED RATHER THAN DUPLICATED. A trace in space is a :class:`~lab_commons.viz.Series`
carrying a ``z``: matplotlib itself spells the two as one method (``plot(x, y)`` and
``plot(x, y, z)``), the fields a curve carries (colour, width, dash, label, alpha) are the same
fields, and a second dataclass holding them would be one idea with two spellings — the duplication
this whole tier exists to remove. The rank is not silent either way: a plane frame REFUSES a trace
that carries a z, and a 3D frame refuses one that does not, so neither arity can quietly drop a
coordinate.

THE ONE SHAPE NOTHING ELSE HOLDS IS THE CONNECTED SURFACE. A :class:`~lab_commons.viz.Patch` is ONE
polygon whose boundary a producer already knows; a :class:`Mesh` is a vertex set AND the faces that
INDEX it, which is the shape a meshing layer holds (n vertices, m triangles) and what a solver hands
over. Repeated per face it would be several times the memory to say the same thing, and turning a
mesh into a polygon soup to describe a picture is the producer doing the adapter's job.

WHAT THIS MODULE IS NOT: a place to BUILD geometry. Sweeping a circle along a centreline — the tube
one consumer's 3D backend computes — is a surface GENERATOR, and nothing here draws one: a generator
belongs with the geometry it generates (a mesh layer), and what reaches a frame is the
:class:`Mesh` it produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike

from lab_commons.viz._placement import Rect

if TYPE_CHECKING:  # a type-only import: the vocabulary imports THIS module, and a cycle is not a type
    from collections.abc import Sequence

    from lab_commons.viz import Series

#: The three axes a :class:`Mesh` has, BY NAME — so the rank check in :meth:`Mesh.polygons` compares
#: against names rather than a literal, and the refusal can say what a vertex is a point IN.
_AXES: Final = ('x', 'y', 'z')

#: The two axes a mesh's VERTEX TABLE has, BY NAME for the same reason: one row per point, one
#: column per coordinate of it.
_VERTEX_AXES: Final = ('point', 'coordinate')

#: The two axes a mesh's CONNECTIVITY has, BY NAME for the same reason: one row per face, one column
#: per corner of it. A face list of any other rank has no corners to resolve.
_FACE_AXES: Final = ('face', 'corner')


@dataclass(frozen=True)
class Mesh:
    """A surface in space: a vertex set and the faces that INDEX it — a tessellated body, a plate.

    WHY THIS IS NOT A LIST OF :class:`~lab_commons.viz.Patch`, and the difference is connectivity
    rather than shape. A patch is one polygon that CARRIES its own vertices; a mesh's faces SHARE
    theirs, and that sharing is how the data arrives (a meshing layer holds n vertices and m index
    triples, a tetrahedral cell is four shared corners). Exploding it back into a polygon soup would
    be the producer reshaping a mesh into a picture, and for a tessellated body it is several times
    the memory to say the same thing.

    Attributes:
        vertices: the shared points, shape ``(n, 3)``.
        faces: the connectivity, shape ``(m, k)`` — one row per face, each row an index per corner
            (``k`` is 3 for a triangle, 4 for the quads a swept body makes). An index names a row of
            *vertices*, so a mesh is one vertex set however many faces touch each of them.
        color: the face colour, or ``None`` for an UNFILLED surface — the same statement
            :class:`~lab_commons.viz.Patch` makes with its own ``color``, so a mesh with no colour
            and no edge colour is a wireframe with no wires and a producer states one of the two.
        edgecolor: the colour of the face outlines, or ``None`` for none.
        label: the legend entry; a mesh with no label is not legended.
        alpha: opacity, ``1.0`` for opaque — a set of bodies is read through itself.

    """

    vertices: ArrayLike
    faces: ArrayLike
    color: str | None = None
    edgecolor: str | None = None
    label: str | None = None
    alpha: float = 1.0

    def polygons(self) -> np.ndarray:
        """The faces as point lists, shape ``(m, k, 3)`` — THE ONE PLACE THE TWO ARRAYS ARE READ.

        A face's corners are named by index, so resolving them is the step from the mesh's own form
        to a drawing's, and it is done HERE rather than in an adapter: the arithmetic is one
        indexing operation today and would be one picture per implementation the moment two of them
        spelled it, which is the failure this tier exists to prevent.

        Raises:
            ValueError: when the vertices are not ``(n, 3)``, or the faces are not ``(m, k)`` — a
                mesh of another rank has no surface to draw, and the refusal names the shape that
                does rather than failing inside a library.

        """
        vertices = np.asarray(self.vertices, dtype=float)
        if vertices.ndim != len(_VERTEX_AXES) or vertices.shape[1] != len(_AXES):
            msg = (
                f'a Mesh is points in space -- (n, {len(_AXES)}) vertices; got {vertices.shape}. A '
                f'triangulation in the plane is a Field or a Samples, and one polygon is a Patch'
            )
            raise ValueError(msg)
        faces = np.asarray(self.faces, dtype=int)
        if faces.ndim != len(_FACE_AXES):
            msg = (
                f'a Mesh indexes its faces one row per face and one column per corner -- '
                f'{" x ".join(_FACE_AXES)}; got {faces.shape}'
            )
            raise ValueError(msg)
        return vertices[faces]


@runtime_checkable
class Frame3D(Protocol):
    """ONE 3D COORDINATE SYSTEM ON A CANVAS: a camera, three axes, and the verbs that draw in space.

    THE VERBS ARE ITS OWN, and the pair of them with :class:`~lab_commons.viz.Frame` is what makes
    this a second protocol rather than a third projection: everything a plane frame draws as a shape
    — a bar, a circle, a filled region, a raster, a contour — has no drawing here, and everything
    here (a camera, a box aspect, a surface) has none there.

    THE TWO AXIS VERBS THAT ARE NOT SHARED ARE THE CAMERA AND THE BOX: ``set_view`` points the view
    and ``set_box_aspect`` says how long each axis is drawn, and a 3D figure that states neither is
    read off the library's own default angle — which is a figure, but not a described one.

    Attributes:
        rect: where this frame sits on the canvas, ``(left, bottom, width, height)`` in canvas
            fractions — or ``None`` when the producer left the placement to the canvas, exactly as
            :attr:`~lab_commons.viz.Frame.rect` means it.
        projection: this frame's coordinate system — ``'3d'``, the one a
            :class:`~lab_commons.viz.Frame` is never built in and ``frame()`` refuses by name.

    """

    rect: Rect | None
    projection: str

    def set_title(self, text: str) -> None:
        """Set the panel's title."""

    def set_xlabel(self, text: str) -> None:
        """Set the x axis's label."""

    def set_ylabel(self, text: str) -> None:
        """Set the y axis's label."""

    def set_zlabel(self, text: str) -> None:
        """Set the z axis's label — the one axis a plane frame does not have."""

    def set_limits(
        self,
        *,
        x: tuple[float, float] | None = None,
        y: tuple[float, float] | None = None,
        z: tuple[float, float] | None = None,
    ) -> None:
        """Fix the axis limits; a pair given high-to-low INVERTS that axis — one more axis, same verb."""

    def set_view(self, *, elev: float, azim: float) -> None:
        """Point the camera: *elev* above the xy plane and *azim* around it, both in degrees."""

    def set_box_aspect(self, aspect: tuple[float, float, float]) -> None:
        """Draw the axes in the ratio *aspect* — the length of one x, y and z unit on the page."""

    def legend(self, *, on: bool = True) -> None:
        """Show or hide the legend of everything labelled so far."""

    def draw_lines(self, lines: Sequence[Series]) -> None:
        """Draw *lines* as ONE set of traces — each a :class:`~lab_commons.viz.Series` with a z.

        THE CALL IS THE GROUP, AND ONE ARTIST DRAWS IT. A collection is what makes a figure of many
        conductors cheap (one artist rather than one per trace, measured at ~4x the draw time for
        200 of them), and it is why this verb is the collection where :meth:`~lab_commons.viz.Frame.draw_line`
        is the single trace: a set of traces drawn as ONE artist is ONE legend row, so the lines of
        one call share their label — a producer drawing two groups makes two calls.

        A line that carries no ``z`` is REFUSED rather than drawn at zero: a plane trace on this frame
        would be a coordinate nobody stated.
        """

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols at its points in space — the cloud, which is one artist already."""

    def draw_meshes(self, meshes: Sequence[Mesh]) -> None:
        """Draw *meshes* as surfaces — each its own body, with its own vertices and faces."""


class NullFrame3D:
    """A 3D coordinate system that accepts every verb and draws nothing — what the default hands back.

    TOTAL BY CONSTRUCTION, for the reason :class:`~lab_commons.viz.NullFrame` gives: a producer
    declares its figure ONCE, unconditionally, and "does this box draw?" is answered by which canvas
    it built rather than by a branch at every drawing call. A batch that renders conductor previews
    must run on the box that has no plotting library at all.

    THE RECT AND THE PROJECTION ARE KEPT and not the camera: what the production code asked for is
    the placement and the coordinate system, while a view and a box aspect are DRAWING decisions this
    class has nothing to resolve them for.
    """

    def __init__(self, *, rect: Rect, projection: str) -> None:
        """Carry the placement and the coordinate system nothing is drawn on."""
        self.rect = rect
        self.projection = projection

    def set_title(self, text: str) -> None:
        """Accepted and ignored."""

    def set_xlabel(self, text: str) -> None:
        """Accepted and ignored."""

    def set_ylabel(self, text: str) -> None:
        """Accepted and ignored."""

    def set_zlabel(self, text: str) -> None:
        """Accepted and ignored."""

    def set_limits(
        self,
        *,
        x: tuple[float, float] | None = None,
        y: tuple[float, float] | None = None,
        z: tuple[float, float] | None = None,
    ) -> None:
        """Accepted and ignored."""

    def set_view(self, *, elev: float, azim: float) -> None:
        """Accepted and ignored."""

    def set_box_aspect(self, aspect: tuple[float, float, float]) -> None:
        """Accepted and ignored."""

    def legend(self, *, on: bool = True) -> None:
        """Accepted and ignored."""

    def draw_lines(self, lines: Sequence[Series]) -> None:
        """Accepted and ignored."""

    def draw_markers(self, series: Series) -> None:
        """Accepted and ignored."""

    def draw_meshes(self, meshes: Sequence[Mesh]) -> None:
        """Accepted and ignored."""
