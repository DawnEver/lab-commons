"""The matplotlib 3D frame — ONE coordinate system with a third axis, on an ``MplRenderer``.

WHY THIS IS A MODULE OF ITS OWN, and it is the band's seam rather than a taste: the plane frame
(:mod:`lab_commons.viz.mpl_frame`) is at the repo's module-size band, and this is a second frame
CLASS on the same canvas rather than more verbs on the first — the joint the tier already names is
"one coordinate system per class", so the file follows the concept.

THE IMPORT AT THE TOP OF THIS MODULE IS ALSO WHAT REGISTERS THE PROJECTION. ``mpl_toolkits.mplot3d``
is the module that teaches matplotlib the name ``'3d'``; importing its collection classes here means
the canvas can ask for that projection from the moment ``lab_commons.viz.mpl`` is imported, and there
is no second place to forget it. It ships WITH matplotlib, so it is not a dependency of its own —
which is why this tier's 3D support costs no new one.

WHAT IS SHARED WITH THE PLANE FRAME, AND IT IS THE THINGS A SECOND COPY WOULD DRIFT ON: the canvas's
palette cursor (:func:`lab_commons.viz.mpl_frame._next_color`) and the display policy the canvas
owns. What differs is the verbs, and only they are written here.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import matplotlib as mpl
import numpy as np
from matplotlib.axes import Axes
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

from lab_commons.viz import Mesh, Rect, Series, Style
from lab_commons.viz.mpl_frame import _next_color

__all__ = ['MplFrame3D']


class MplFrame3D:
    """One 3D coordinate system on a matplotlib canvas: an ``Axes3D`` and the verbs that draw in space.

    Attributes:
        axes: the matplotlib ``Axes3D`` itself — the adapter's own escape hatch, for the one call the
            vocabulary does not carry yet (a texture, a shading, a text pinned to the axes).
        rect: where this frame sits on the canvas, ``(left, bottom, width, height)`` in fractions —
            or ``None`` when the producer left the placement to the canvas (see
            :meth:`~lab_commons.viz.mpl.MplRenderer.frame_3d`).
        projection: this frame's coordinate system, ``'3d'`` — the name a plane
            :class:`~lab_commons.viz.Frame` is never built in.

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
        """Bind a 3D axes to the canvas's style and to the canvas's palette cursor."""
        self.axes = axes
        self.rect = rect
        self.projection = projection
        self._style = style
        self._drawn = drawn

    def set_title(self, text: str) -> None:
        """Set this frame's title."""
        self.axes.set_title(text)

    def set_xlabel(self, text: str) -> None:
        """Set the x axis's label."""
        self.axes.set_xlabel(text)

    def set_ylabel(self, text: str) -> None:
        """Set the y axis's label."""
        self.axes.set_ylabel(text)

    def set_zlabel(self, text: str) -> None:
        """Set the z axis's label — the axis a plane frame has no counterpart for."""
        self.axes.set_zlabel(text)

    def set_limits(
        self,
        *,
        x: tuple[float, float] | None = None,
        y: tuple[float, float] | None = None,
        z: tuple[float, float] | None = None,
    ) -> None:
        """Fix the axis limits; a pair given high-to-low inverts that axis, which matplotlib honours."""
        if x is not None:
            self.axes.set_xlim(x)
        if y is not None:
            self.axes.set_ylim(y)
        if z is not None:
            self.axes.set_zlim(z)

    def set_view(self, *, elev: float, azim: float) -> None:
        """Point the camera: elevation above the xy plane and azimuth around it, both in degrees."""
        self.axes.view_init(elev=elev, azim=azim)

    def set_box_aspect(self, aspect: tuple[float, float, float]) -> None:
        """Draw one x, y and z unit in the ratio *aspect* — the page's proportions, not the data's."""
        self.axes.set_box_aspect(aspect)

    def legend(self, *, on: bool = True) -> None:
        """Show the legend of everything labelled on this frame, or remove it."""
        existing = self.axes.get_legend()
        if on:
            self.axes.legend()
        elif existing is not None:
            existing.remove()

    def draw_lines(self, lines: Sequence[Series]) -> None:
        """Draw *lines* as ONE collection — one artist, one legend row, the traces coloured apart.

        THE LABEL IS THE GROUP'S, WHICH IS WHY THE CALL AGREES ON ONE: a collection carries a single
        legend entry, so two labels in one call could only be drawn by dropping one -- refused here
        rather than resolved silently, and the remedy is one call per group.

        ``Line3DCollection`` TAKES A POLYLINE PER MEMBER, so each :class:`Series` reaches it as its
        own point path rather than as segment pairs: the collection is a drawing of the same traces
        the plane frame's ``plot`` draws, in one artist.
        """
        labels = {line.label for line in lines if line.label is not None}
        if len(labels) > 1:
            msg = (
                f'a set of traces drawn as one artist is ONE legend row, and these carry {sorted(labels)}: '
                'draw one call per group, or label them all the same'
            )
            raise ValueError(msg)
        collection = Line3DCollection(
            [self._space(line) for line in lines],
            colors=[_next_color(self._drawn, self._style, line.color) for line in lines],
            linewidths=[self._width(line.width) for line in lines],
            linestyles=[line.style for line in lines],
            alpha=[line.alpha for line in lines],
        )
        if labels:
            collection.set_label(labels.pop())
        self.axes.add_collection3d(collection)

    def draw_markers(self, series: Series) -> None:
        """Draw *series* as symbols at its points in space — a cloud is one artist already.

        ``size`` IS A LENGTH HERE AND AN AREA THERE, the same conversion the plane frame's
        :meth:`~lab_commons.viz.Frame.draw_samples` makes: this library's ``scatter(s=...)`` takes a
        marker's area in points squared, and the vocabulary states a length.
        """
        points = self._space(series)
        self.axes.scatter(
            points[:, 0],
            points[:, 1],
            points[:, 2],
            color=_next_color(self._drawn, self._style, series.color),
            marker=series.marker or 'o',
            s=None if series.size is None else float(series.size) ** 2,
            label=series.label,
            alpha=series.alpha,
        )

    def draw_meshes(self, meshes: Sequence[Mesh]) -> None:
        """Draw *meshes* as surfaces — a body each, its faces resolved by the primitive.

        EACH MESH IS ITS OWN ARTIST, because each has its own vertex set and its own colour, and this
        library sorts a collection's faces by depth within it: merging two bodies into one collection
        would let the sort decide which of them is in front.

        AN UNCOLOURED MESH IS AN UNFILLED ONE, the same statement :class:`~lab_commons.viz.Patch`
        makes: this library reads a ``None`` face colour as "no fill", so a mesh with neither a colour
        nor an edge colour is a wireframe with no wires — state one of the two.
        """
        for mesh in meshes:
            collection = Poly3DCollection(
                mesh.polygons(),
                facecolors=mesh.color,
                edgecolors=mesh.edgecolor,
                alpha=mesh.alpha,
            )
            if mesh.label is not None:
                collection.set_label(mesh.label)
            self.axes.add_collection3d(collection)

    def _space(self, series: Series) -> np.ndarray:
        """*series* as ``(k, 3)`` points — and a trace with no ``z`` is REFUSED, never drawn at zero.

        THE OTHER HALF OF THE ARITY CONTRACT :func:`lab_commons.viz.mpl_frame._plane` enforces for the
        plane frame: between them, a trace cannot be drawn on the wrong one of the two, and the
        refusal names the canvas verb that builds the frame it belongs on.
        """
        if series.z is None:
            msg = (
                'this frame draws in space and the Series it was handed carries no z: drawing it at z = 0 '
                'would be a coordinate nobody stated. A trace in the plane belongs on a plane frame: '
                'figure.frame()'
            )
            raise ValueError(msg)
        return np.column_stack(
            [np.asarray(series.x, dtype=float), np.asarray(series.y, dtype=float), np.asarray(series.z, dtype=float)]
        )

    def _width(self, width: float | None) -> float:
        """*width*, or this library's own line width — a collection has no per-artist default to take."""
        return mpl.rcParams['lines.linewidth'] if width is None else width
