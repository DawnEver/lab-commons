"""``lab_commons.viz`` — the vocabulary, the contract, and the renderer that draws nothing.

THIS FILE IMPORTS NO PLOTTING LIBRARY, which is the layer's own claim being measured rather than
described: the vocabulary is what a producer depends on, so it has to be usable -- and testable --
on a box where matplotlib and bokeh were never installed.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from _viz_figure import draw_everything

from lab_commons.viz import (
    Bars,
    Circle,
    Field,
    Label,
    NullRenderer,
    Patch,
    Renderer,
    Scale,
    Segment,
    Series,
    Style,
)


def test_the_null_renderer_satisfies_the_protocol() -> None:
    """``isinstance`` against the Protocol is what makes "this is a renderer" an ANSWER.

    Structural and runtime-checkable, so a class that has lost a verb is refused at the check rather
    than at the first producer that happens to call the missing one.
    """
    assert isinstance(NullRenderer(), Renderer)


def test_the_null_renderer_accepts_every_primitive_and_writes_nothing(tmp_path: Path) -> None:
    """The default renderer is driven through the WHOLE vocabulary, and no file appears.

    A default that could not be driven through a figure description would push ``if plot:`` branches
    back into every producer, which is the shape this class exists to remove.
    """
    renderer = NullRenderer()
    draw_everything(renderer)
    target = tmp_path / 'figure.png'
    assert renderer.save(target) is None, 'a renderer that wrote nothing must not claim a path'
    assert not target.exists(), 'the null renderer wrote a file'
    renderer.show()
    renderer.close()


def test_every_primitive_is_frozen() -> None:
    """A description handed to a renderer is not shared mutable state between producer and drawer."""
    series = Series(x=[0.0, 1.0], y=[0.0, 1.0])
    with pytest.raises(dataclasses.FrozenInstanceError):
        series.label = 'edited'


def test_the_primitives_default_to_the_neutral_choice() -> None:
    """Every optional field means "let the renderer decide", so a minimal description is complete."""
    assert Series(x=[0.0], y=[0.0]).color is None
    assert Series(x=[0.0], y=[0.0]).style == '-'
    assert Series(x=[0.0], y=[0.0]).label is None
    assert Patch(vertices=[[0.0, 0.0]]).color is None
    assert Circle(x=0.0, y=0.0, radius=1.0).edgecolor is None
    assert Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0).arrow is False
    assert Label(x=0.0, y=0.0, text='A').halign == 'center'
    assert Field(x=[0.0], y=[0.0], values=[1.0]).scale is None
    # The ONE field that does not defer: the two libraries' own bar widths disagree, so a None here
    # would be a description that draws a different picture per backend.
    assert Bars(x=[0.0], height=[1.0]).width == 0.8


def test_scale_is_what_makes_two_figures_comparable() -> None:
    """The range travels WITH the field, which is the difference between a map and a picture."""
    pinned = Scale(cmap='viridis', label='|B| (T)', vmin=0.0, vmax=2.0)
    assert pinned.vmin == 0.0
    assert pinned.vmax == 2.0
    assert Scale().vmin is None, 'an unpinned scale is derived from the samples, and says so'


def test_the_style_cycles_one_palette_for_every_adapter() -> None:
    """One palette, index-stable: a series keeps its colour whatever drew it and however many ran."""
    style = Style()
    assert len(set(style.palette)) == len(style.palette), 'two series would share a colour'
    assert style.color(0) == style.palette[0]
    assert style.color(len(style.palette)) == style.palette[0], 'the cycle wraps rather than raising'
    assert Style(palette=('#000000', '#ffffff')).color(3) == '#ffffff'


def test_the_style_defaults_are_this_librarys_decision() -> None:
    """The one place the family's figure size, resolution and type size are stated."""
    style = Style()
    assert style.figure_size == (8.0, 6.0)
    assert style.dpi == 100
    assert style.font_size == 10.0
    assert style.font_family[0] == 'Times New Roman'
