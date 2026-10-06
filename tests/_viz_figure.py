"""ONE figure description, covering every subject the plotting vocabulary exists for.

WHY IT IS SHARED RATHER THAN REPEATED. The layer's whole claim is that a description is independent
of the library that draws it: the same calls must produce a chart, a waveform, a field map, a winding
layout and a connection diagram through either adapter. A description written once and driven through
both is that claim MEASURED; a description written twice, once per adapter, would agree with itself
by construction and measure nothing.

IT DRAWS THROUGH :class:`~lab_commons.viz.Renderer`, never through a concrete adapter, so this
module imports no plotting library -- which is also what keeps the vocabulary's own test file free
of one.

EVERY VERB BOTH ADAPTERS CAN DRAW IS HERE, AND THE ONE THAT IS NOT IS A FINDING RATHER THAN AN
OMISSION. ``Contours`` asks a point set for isolines, which bokeh cannot compute without a dependency
neither extra declares, so that adapter REFUSES it by name (see ``lab_commons/viz/bokeh.py``'s module
docstring). Adding that call here would turn "the two adapters draw the same description" into "the
two adapters draw the same description except this one", and would make the peer test the place the
exception is hidden -- so the verb is exercised PER ADAPTER instead: drawn by matplotlib, refused by
bokeh, with the refusal itself under test.
"""

from __future__ import annotations

from lab_commons.viz import (
    Bars,
    Circle,
    Colorbar,
    Field,
    Label,
    Patch,
    Renderer,
    Scale,
    Segment,
    Series,
    Ticks,
    Vectors,
)

#: A square of four samples, so a field map has a scale to draw rather than a single value.
_FIELD = Field(
    x=[0.0, 1.0, 0.0, 1.0],
    y=[0.0, 0.0, 1.0, 1.0],
    values=[0.0, 1.0, 1.0, 2.0],
    scale=Scale(cmap='viridis', label='|B| (T)', vmin=0.0, vmax=2.0),
)

#: Two coil sides, one by phase name and one outlined -- the winding layout's two colouring modes.
_SLOTS = (
    Patch(vertices=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], color='#1f77b4', label='phase A'),
    Patch(vertices=[[2.0, 0.0], [3.0, 0.0], [3.0, 1.0], [2.0, 1.0]], edgecolor='black'),
)

#: One vector in each quadrant, so the quiver has four directions to draw rather than one.
_VECTORS = Vectors(x=[2.0, 6.0, 2.0, 6.0], y=[2.0, 2.0, 6.0, 6.0], u=[1.0, -1.0, 1.0, -1.0], v=[1.0, 1.0, -1.0, -1.0])


def draw_everything(renderer: Renderer) -> None:
    """Draw every subject both adapters can draw: chart, waveform, field map, layout, connections."""
    renderer.set_title('every primitive')
    renderer.set_xlabel('x')
    renderer.set_ylabel('y')
    renderer.set_limits(x=(0.0, 10.0), y=(0.0, 10.0))
    renderer.set_ticks(x=Ticks(positions=[0.0, 5.0, 10.0], labels=['a', 'b', 'c']))
    renderer.set_ticks(y=Ticks(positions=[0.0, 10.0], labels=()))
    renderer.grid()

    # A CHART: bars beside a line and a marker cloud, which is the axis-bearing half.
    renderer.draw_bars(Bars(x=[1.0, 2.0], height=[3.0, 4.0], label='bars', alpha=0.5))
    renderer.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0], label='trend'))
    renderer.draw_markers(Series(x=[5.0], y=[5.0], marker='s', label='samples', alpha=0.8))

    # A WAVEFORM: two traces over one axis, distinguished by colour, dash and legend.
    renderer.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 1.0, 0.0], label='phase A'))
    renderer.draw_line(Series(x=[0.0, 1.0, 2.0], y=[1.0, 0.0, 1.0], style='--', label='phase B'))

    # A FIELD MAP: samples, and what their colour means.
    renderer.draw_field(_FIELD)

    # A BAR FOR A SCALE THE FIGURE SET ITSELF, once continuous and once banded on its ticks.
    renderer.draw_colorbar(Colorbar(scale=Scale(cmap='viridis', label='scale', vmin=0.0, vmax=2.0)))
    renderer.draw_colorbar(
        Colorbar(
            scale=Scale(cmap='viridis', label='layer', vmin=0.0, vmax=3.0),
            ticks=[0.0, 1.0, 2.0, 3.0],
            tick_labels=['1', '2', '3', '4'],
        )
    )

    # A VECTOR FIELD: a direction and a magnitude per sample, drawn as arrows.
    renderer.draw_vectors(_VECTORS)

    # A WINDING LAYOUT: filled regions, a hatched patch, a coil side, a label, no axes.
    renderer.set_equal_aspect()
    renderer.set_axis_off()
    renderer.draw_patches(_SLOTS)
    renderer.draw_patches((Patch(vertices=[[4.0, 4.0], [4.5, 4.0], [4.5, 4.5]], color='white', hatch='/'),))
    renderer.draw_circles((Circle(x=1.5, y=1.5, radius=0.2, label='coil side'),))
    renderer.draw_labels((Label(x=0.5, y=0.5, text='A', halign='center', valign='center', box=True),))

    # A CONNECTION DIAGRAM: wires, one of them carrying direction.
    renderer.draw_segments(
        (
            Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0, label='wire'),
            Segment(x0=1.0, y0=1.0, x1=2.0, y1=1.0, arrow=True, alpha=0.6),
        )
    )
    renderer.legend()
