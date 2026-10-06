"""ONE description per SHAPE this layer exists for, driven through BOTH adapters.

WHY THEY ARE SHARED RATHER THAN REPEATED. The layer's whole claim is that a description is
independent of the library that draws it: the same calls must produce a chart, a waveform, a field
map, a winding layout and a connection diagram through either adapter. A description written once and
driven through both is that claim MEASURED; a description written twice, once per adapter, would
agree with itself by construction and measure nothing.

SIX SUBJECTS, AND THE FIVE THAT ARE NOT ONE AXES ARE THE POINT OF THIS FILE NOW. ``draw_everything``
is the verb menu on one coordinate system -- chart, waveform, the two plane shapes (a masked grid map
and a point cloud), a winding layout and a connection diagram. The other five are the shapes an
earlier version of this tier could not express AT ALL, each for the same reason -- a renderer
conflated the canvas with the coordinate system, so there was exactly one axes and it was built at
construction, and the shapes that needed a second one, a different projection or a third axis had no
spelling:

* ``draw_panels`` — four frames in a grid, rows sharing an x axis and columns sharing a y one;
* ``draw_twin`` — two frames over ONE panel, sharing the x axis, the second with its own y scale;
* ``draw_polar`` — a frame in the polar projection, which is one of the subjects a single backend
  cannot draw: bokeh has no polar projection and REFUSES it by name (see ``lab_commons/viz/bokeh.py``),
  exactly as it refuses ``Contours``. That refusal is exercised PER ADAPTER rather than here, so
  "the same description draws through both" does not quietly become "...except this one".
* ``draw_continuum`` — a scalar over a point set, drawn as a surface through it. The second subject
  only one adapter can draw, for the same kind of reason: bokeh has no filled-contour glyph over a
  point set, and it REFUSES ``Field`` by name rather than substituting the point cloud that
  ``Samples`` is.
* ``draw_space`` — a frame with a THIRD AXIS, and the third subject only one adapter can draw: bokeh
  has no 3D axes at all, so ``BokehRenderer.frame_3d`` refuses by name while this description is
  rendered through matplotlib by ``test_viz_mpl``.

IT DRAWS THROUGH THE PROTOCOLS, never through a concrete adapter, so this module imports no plotting
library -- which is also what keeps the vocabulary's own test file free of one.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from lab_commons.viz import (
    Bars,
    Circle,
    Colorbar,
    Field,
    Figure,
    Frame,
    Grid,
    Label,
    Mesh,
    Patch,
    Samples,
    Scale,
    Segment,
    Series,
    Ticks,
    Vectors,
    panel_rects,
)

#: A scalar over a CONTINUUM, with a scale, so the surface it is drawn as has a colour bar too.
_FIELD = Field(
    x=[0.0, 1.0, 0.0, 1.0],
    y=[0.0, 0.0, 1.0, 1.0],
    values=[0.0, 1.0, 1.0, 2.0],
    scale=Scale(cmap='viridis', label='|B| (T)', vmin=0.0, vmax=2.0),
)

#: A 2 x 3 grid WITH A VOID, which is the whole subject of this shape: the masked cell must reach
#: the artist masked, because a grid rendered without its mask draws a value it never had. The
#: extent is stated in data coordinates (a mesh's bounding box, which is where these come from), and
#: the scale is pinned so the two adapters' colour bars are comparable.
_GRID = Grid(
    values=np.ma.masked_array(
        [[0.0, 0.5, 1.0], [1.5, 2.0, 2.5]],
        mask=[[False, False, False], [False, True, False]],
    ),
    extent=(0.0, 3.0, 0.0, 2.0),
    scale=Scale(cmap='viridis', label='|B| (T)', vmin=0.0, vmax=2.5),
)

#: FOUR POINTS THAT CARRY A VALUE, one of them off a line so the cloud is not a curve, with a stated
#: mark size: the colour-mapped point cloud, which is not a ``Field`` and not a coloured ``Series``.
_SAMPLES = Samples(
    x=[0.0, 1.0, 2.0, 3.0],
    y=[0.0, 1.0, 0.5, 1.5],
    values=[0.0, 1.0, 2.0, 3.0],
    scale=Scale(cmap='plasma', label='speed (rpm)', vmin=0.0, vmax=3.0),
    size=6.0,
)

#: Two coil sides, one by phase name and one outlined -- the winding layout's two colouring modes.
_SLOTS = (
    Patch(vertices=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], color='#1f77b4', label='phase A'),
    Patch(vertices=[[2.0, 0.0], [3.0, 0.0], [3.0, 1.0], [2.0, 1.0]], edgecolor='black'),
)

#: One vector in each quadrant, so the quiver has four directions to draw rather than one.
_VECTORS = Vectors(x=[2.0, 6.0, 2.0, 6.0], y=[2.0, 2.0, 6.0, 6.0], u=[1.0, -1.0, 1.0, -1.0], v=[1.0, 1.0, -1.0, -1.0])

#: The rects of the panel grid, in the order :func:`draw_panels` creates them: top-left, top-right,
#: bottom-left, bottom-right. COMPUTED BY THE VOCABULARY RATHER THAN WRITTEN HERE, so the grid below
#: is the panel arithmetic RENDERED through both adapters rather than a second copy of it.
#:
#: THE GAPS ARE THE POINT OF THE NUMBERS. A rect is a frame's BOX, and everything a reader sees
#: around it -- the ticks, the labels, the title -- is drawn OUTSIDE it, so a grid whose panels touch
#: draws the top row's x labels under the bottom row's title. That arithmetic is
#: :func:`~lab_commons.viz.panel_rects`'s, and this constant is what it returns for a 2 x 2 grid.
PANEL_RECTS: Final = panel_rects(2, 2)


def draw_everything(frame: Frame) -> None:
    """Draw every subject both adapters can draw: chart, waveform, two plane shapes, layout, wires."""
    frame.set_title('every primitive')
    frame.set_xlabel('x')
    frame.set_ylabel('y')
    frame.set_limits(x=(0.0, 10.0), y=(0.0, 10.0))
    frame.set_ticks(x=Ticks(positions=[0.0, 5.0, 10.0], labels=['a', 'b', 'c']))
    frame.set_ticks(y=Ticks(positions=[0.0, 10.0], labels=()))
    frame.grid()

    # A CHART: bars beside a line and a marker cloud, which is the axis-bearing half.
    frame.draw_bars(Bars(x=[1.0, 2.0], height=[3.0, 4.0], label='bars', alpha=0.5))
    frame.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0], label='trend'))
    frame.draw_markers(Series(x=[5.0], y=[5.0], marker='s', label='samples', alpha=0.8))

    # A WAVEFORM: two traces over one axis, distinguished by colour, dash and legend.
    frame.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 1.0, 0.0], label='phase A'))
    frame.draw_line(Series(x=[0.0, 1.0, 2.0], y=[1.0, 0.0, 1.0], style='--', label='phase B'))

    # A GRID MAP: a scalar on a rectilinear grid, with a VOID that must be drawn as nothing.
    frame.draw_grid(_GRID)

    # A POINT CLOUD: marks that carry a value, coloured by it, with the scale that makes it readable.
    frame.draw_samples(_SAMPLES)

    # A BAR FOR A SCALE THE FIGURE SET ITSELF, once continuous and once banded on its ticks.
    frame.draw_colorbar(Colorbar(scale=Scale(cmap='viridis', label='scale', vmin=0.0, vmax=2.0)))
    frame.draw_colorbar(
        Colorbar(
            scale=Scale(cmap='viridis', label='layer', vmin=0.0, vmax=3.0),
            ticks=[0.0, 1.0, 2.0, 3.0],
            tick_labels=['1', '2', '3', '4'],
        )
    )

    # A VECTOR FIELD: a direction and a magnitude per sample, drawn as arrows.
    frame.draw_vectors(_VECTORS)

    # A WINDING LAYOUT: filled regions, a hatched patch, a coil side, a label, no axes.
    frame.set_equal_aspect()
    frame.set_axis_off()
    frame.draw_patches(_SLOTS)
    frame.draw_patches((Patch(vertices=[[4.0, 4.0], [4.5, 4.0], [4.5, 4.5]], color='white', hatch='/'),))
    frame.draw_circles((Circle(x=1.5, y=1.5, radius=0.2, label='coil side'),))
    frame.draw_labels((Label(x=0.5, y=0.5, text='A', halign='center', valign='center', box=True),))

    # A CONNECTION DIAGRAM: wires, one of them carrying direction.
    frame.draw_segments(
        (
            Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0, label='wire'),
            Segment(x0=1.0, y0=1.0, x1=2.0, y1=1.0, arrow=True, alpha=0.6),
        )
    )
    frame.legend()


def draw_panels(figure: Figure) -> None:
    """A GRID OF PANELS: two rows of two, each row sharing an x axis and each column a y axis.

    THE SHAPE A SOLVER COMPARISON NEEDS, and the one the ~10 measured ``subplots(nrows, ncols,
    sharex=True, sharey=True)`` sites in the consumer ask for: four pictures of one quantity, read
    row against row and column against column, where a reader compares the panels by their SHAPE
    rather than by their tick numbers.

    THE FRAMES ARE CREATED IN READING ORDER (top-left, top-right, bottom-left, bottom-right) so that
    ``figure.frames`` is the picture's own order; the rects are :data:`PANEL_RECTS`, which is
    :func:`~lab_commons.viz.panel_rects`'s answer for a 2 x 2 grid — so the panel arithmetic is
    RENDERED through both adapters here rather than restated. Every panel shares BOTH axes with the
    first one, which is what ``subplots(2, 2, sharex=True, sharey=True)`` means and what makes the
    four one comparison instead of four charts: a reader reads the same angle and the same flux
    density off whichever panel they happen to look at.
    """
    top_left = figure.frame(rect=PANEL_RECTS[0])
    top_right = figure.frame(rect=PANEL_RECTS[1], sharex=top_left, sharey=top_left)
    bottom_left = figure.frame(rect=PANEL_RECTS[2], sharex=top_left, sharey=top_left)
    bottom_right = figure.frame(rect=PANEL_RECTS[3], sharex=top_left, sharey=top_left)

    for index, panel in enumerate((top_left, top_right, bottom_left, bottom_right)):
        panel.set_title(f'panel {index}')
        panel.set_ylabel('|B| (T)')
        panel.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 1.0 + index, 0.0], label='field'))
    bottom_left.set_xlabel('angle (deg)')
    bottom_right.set_xlabel('angle (deg)')


def draw_twin(figure: Figure) -> None:
    """A TWIN AXIS: two frames over ONE panel, sharing the x axis, the second with its own y scale.

    THE SHAPE THE 2 MEASURED ``twinx`` SITES ASK FOR — torque on the left and power on the right
    against one speed axis — and it is spelled by NAMING NO RECT: a frame that shares an x axis and
    says nothing about where it goes is drawn over the frame it shares with, which is what makes the
    two y scales one picture.

    The second frame is a different quantity with a different unit, so its scale is its own: nothing
    here shares a y axis, and the limits below are set on one frame only to prove it.
    """
    torque = figure.frame()
    power = figure.frame(sharex=torque)

    torque.set_title('torque and power against speed')
    torque.set_xlabel('speed (rpm)')
    torque.set_ylabel('torque (Nm)')
    power.set_ylabel('power (kW)')
    torque.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 3.0, 1.0], label='torque'))
    power.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 0.5, 1.0], label='power'))


def draw_polar(figure: Figure) -> None:
    """A POLAR frame: the slot-EMF star — angle IS the slot, radius is a value.

    A cartesian frame can only fake it by drawing the circle by hand, which is exactly the kind of
    thing a producer should not be doing.

    ONE OF THE TWO SUBJECTS ONLY ONE ADAPTER CAN DRAW, and saying so here rather than omitting it is
    the point: the vocabulary expresses it, matplotlib's polar projection draws it, and bokeh refuses
    it BY NAME (it has no polar projection and draws every glyph in cartesian data units). A test
    that drove this through the bokeh adapter would be measuring a promise nobody can keep.
    """
    star = figure.frame(projection='polar')
    star.set_title('slot EMF star')
    star.draw_line(Series(x=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0], y=[1.0, 2.0, 1.5, 2.5, 1.0, 1.5], label='slot EMF'))
    star.draw_markers(Series(x=[0.0, 1.0, 2.0], y=[1.0, 2.0, 1.5], marker='o'))


def draw_continuum(figure: Figure) -> None:
    """A FIELD MAP: a scalar over a CONTINUUM, drawn as a surface through its samples.

    THE OTHER SUBJECT ONLY ONE ADAPTER CAN DRAW. The shape is a point set with values and the drawing
    it asks for is a surface — a filled contour over a triangulation — and bokeh has no such glyph
    (it interpolates with ``contourpy``, which no extra of this package declares), so it REFUSES
    ``Field`` by name. That refusal is the split's consequence rather than a gap: the cloud of marks
    a bokeh field map used to be drawn as is now the ``Samples`` shape, which the producer asks for
    explicitly — see ``test_viz_bokeh`` for the refusal itself.

    The samples are deliberately NOT a rectangle, so the surface is drawn through a triangulation and
    not as a grid: a rectangular array is ``Grid``, and this subject would otherwise be it.
    """
    map_frame = figure.frame()
    map_frame.set_title('|B| over the air gap')
    map_frame.set_xlabel('x (mm)')
    map_frame.set_ylabel('y (mm)')
    map_frame.draw_field(_FIELD)


#: TWO GROUPS OF CONDUCTORS IN SPACE, each trace the consumer's own data shape: a centreline's points,
#: one per sample, with the third coordinate that makes it a trace IN SPACE rather than one in the
#: plane. THE GROUPS ARE THE CALLS — a set of traces drawn as one artist is one legend row, so the
#: two phases are two calls rather than one call of three differently-labelled traces.
_PHASE_U: Final = (
    Series(x=[0.0, 1.0, 2.0, 3.0], y=[0.0, 1.0, 1.0, 0.0], z=[0.0, 0.0, 1.0, 1.0], label='phase U'),
    Series(x=[0.0, 1.0, 2.0, 3.0], y=[2.0, 1.0, 1.0, 2.0], z=[1.0, 1.0, 0.0, 0.0], label='phase U'),
)
_PHASE_V: Final = (Series(x=[0.0, 1.0, 2.0, 3.0], y=[4.0, 3.0, 3.0, 4.0], z=[0.0, 1.0, 0.0, 1.0], label='phase V'),)

#: The weld nodes, as marks that carry no value of their own — a coloured ``Series`` in space, which is
#: the same statement ``draw_markers`` makes in the plane.
_NODES_3D: Final = Series(x=[0.5, 2.5], y=[0.5, 3.5], z=[0.5, 0.5], marker='s', size=6.0, label='weld node')

#: ONE BODY'S SURFACE: a tetrahedron, which is a vertex set AND the faces that index it — the shape a
#: tessellation arrives in, and the one nothing else in the vocabulary holds.
_CELL: Final = Mesh(
    vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    faces=[[0, 1, 2], [0, 1, 3], [0, 2, 3]],
    color='#c8d8e8',
    edgecolor='black',
    label='cell',
)


def draw_space(figure: Figure) -> None:
    """A FRAME WITH A THIRD AXIS: conductors in space, which a plane frame cannot draw at all.

    THE THIRD SUBJECT ONLY ONE ADAPTER CAN DRAW, and for the same kind of reason the polar frame and
    the continuum are: bokeh has no 3D axes, so ``BokehRenderer.frame_3d`` refuses BY NAME at the
    canvas verb that would build one — see ``lab_commons/viz/bokeh.py`` for the message and
    ``test_viz_bokeh`` for the refusal itself. The frame is asked for by its OWN verb rather than as
    ``frame(projection='3d')``, which is refused with that verb as the remedy: the verbs a 3D frame
    has and the ones it lacks are not the plane frame's, so the two are two protocols.

    WHAT IT DRAWS IS THE CONSUMER'S OWN 3D SUBJECT -- conductor centrelines grouped by phase, the
    weld nodes, and a body's surface -- so the vocabulary is measured against the figure that has to
    merge into it rather than against a shape invented here. Every trace carries a ``z``; a 3D frame
    refuses one that does not, exactly as a plane frame refuses one that does.
    """
    windings = figure.frame_3d()
    windings.set_title('conductors in space')
    windings.set_xlabel('x (mm)')
    windings.set_ylabel('y (mm)')
    windings.set_zlabel('z (mm)')
    windings.set_limits(x=(0.0, 4.0), y=(0.0, 4.0), z=(0.0, 2.0))
    windings.set_view(elev=22.0, azim=35.0)
    windings.set_box_aspect((1.0, 1.0, 0.7))
    windings.draw_lines(_PHASE_U)
    windings.draw_lines(_PHASE_V)
    windings.draw_markers(_NODES_3D)
    windings.draw_meshes((_CELL,))
    windings.legend()
