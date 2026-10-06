"""ONE figure description, covering every subject the plotting vocabulary exists for.

WHY IT IS SHARED RATHER THAN REPEATED. The layer's whole claim is that a description is independent
of the library that draws it: the same calls must produce a chart, a waveform, a field map, a winding
layout and a connection diagram through either adapter. A description written once and driven through
both is that claim MEASURED; a description written twice, once per adapter, would agree with itself
by construction and measure nothing.

IT DRAWS THROUGH :class:`~lab_commons.viz.Renderer`, never through a concrete adapter, so this
module imports no plotting library -- which is also what keeps the vocabulary's own test file free
of one.
"""

from __future__ import annotations

from lab_commons.viz import (
    Bars,
    Circle,
    Field,
    Label,
    Patch,
    Renderer,
    Scale,
    Segment,
    Series,
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


def draw_everything(renderer: Renderer) -> None:
    """Draw all five subjects through *renderer*: chart, waveform, field map, layout, connections."""
    renderer.set_title('every primitive')
    renderer.set_xlabel('x')
    renderer.set_ylabel('y')
    renderer.set_limits(x=(0.0, 10.0), y=(0.0, 10.0))
    renderer.grid()

    # A CHART: bars beside a line and a marker cloud, which is the axis-bearing half.
    renderer.draw_bars(Bars(x=[1.0, 2.0], height=[3.0, 4.0], label='bars'))
    renderer.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0], label='trend'))
    renderer.draw_markers(Series(x=[5.0], y=[5.0], marker='s', label='samples'))

    # A WAVEFORM: two traces over one axis, distinguished by colour, dash and legend.
    renderer.draw_line(Series(x=[0.0, 1.0, 2.0], y=[0.0, 1.0, 0.0], label='phase A'))
    renderer.draw_line(Series(x=[0.0, 1.0, 2.0], y=[1.0, 0.0, 1.0], style='--', label='phase B'))

    # A FIELD MAP: samples, and what their colour means.
    renderer.draw_field(_FIELD)

    # A WINDING LAYOUT: filled regions, a coil side, a label, equal aspect and no axes.
    renderer.set_equal_aspect()
    renderer.set_axis_off()
    renderer.draw_patches(_SLOTS)
    renderer.draw_circles((Circle(x=1.5, y=1.5, radius=0.2, label='coil side'),))
    renderer.draw_labels((Label(x=0.5, y=0.5, text='A', halign='center', valign='center'),))

    # A CONNECTION DIAGRAM: wires, one of them carrying direction.
    renderer.draw_segments(
        (
            Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0, label='wire'),
            Segment(x0=1.0, y0=1.0, x1=2.0, y1=1.0, arrow=True),
        )
    )
    renderer.legend()
