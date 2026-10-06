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
    Colorbar,
    Contours,
    Field,
    Label,
    NullRenderer,
    Patch,
    Renderer,
    Scale,
    Segment,
    Series,
    Style,
    Ticks,
    Vectors,
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
    # THE ONE SUBJECT THE SHARED DESCRIPTION CANNOT CARRY is driven here, because every verb --
    # including the one an adapter refuses to DRAW -- has to be a verb this renderer ACCEPTS, or a
    # batch that declares its figures unconditionally would fail on the box it runs unattended on.
    renderer.draw_contours(Contours(x=[0.0, 1.0], y=[0.0, 1.0], values=[0.0, 1.0]))
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
    assert Series(x=[0.0], y=[0.0]).alpha == 1.0
    assert Patch(vertices=[[0.0, 0.0]]).color is None
    assert Patch(vertices=[[0.0, 0.0]]).hatch is None
    assert Patch(vertices=[[0.0, 0.0]]).alpha == 1.0
    assert Circle(x=0.0, y=0.0, radius=1.0).edgecolor is None
    assert Segment(x0=0.0, y0=0.0, x1=1.0, y1=1.0).arrow is False
    assert Label(x=0.0, y=0.0, text='A').halign == 'center'
    assert Label(x=0.0, y=0.0, text='A').box is False, 'a chip nobody asked for is a box on every label'
    assert Field(x=[0.0], y=[0.0], values=[1.0]).scale is None
    assert Contours(x=[0.0], y=[0.0], values=[1.0]).levels == 10
    assert Contours(x=[0.0], y=[0.0], values=[1.0]).color is None
    assert Vectors(x=[0.0], y=[0.0], u=[1.0], v=[0.0]).scale == 1.0
    assert Ticks(positions=[0.0]).labels is None, 'an unlabelled axis is numbered, not silenced'
    # The ONE field that does not defer: the two libraries' own bar widths disagree, so a None here
    # would be a description that draws a different picture per backend.
    assert Bars(x=[0.0], height=[1.0]).width == 0.8


def test_a_ticks_empty_label_list_is_not_an_absent_one() -> None:
    """``None`` numbers the ticks and ``()`` silences them, because the two are different figures."""
    silenced = Ticks(positions=[0.0, 1.0], labels=())
    numbered = Ticks(positions=[0.0, 1.0])
    assert silenced.labels == ()
    assert numbered.labels is None
    assert silenced.labels is not None, 'an empty sequence collapsed into "number them"'


def test_a_bar_bands_its_ticks_by_the_rule_both_adapters_read() -> None:
    """The discrete geometry is the PRIMITIVE's, so two adapters cannot draw two pictures of it."""
    edges, centers = Colorbar(scale=Scale(vmin=0.0, vmax=3.0), ticks=[0.0, 1.0, 2.0, 3.0]).bands()
    assert centers == (0.0, 1.0, 2.0, 3.0)
    assert edges == (-0.5, 0.5, 1.5, 2.5, 3.5), 'each band is centred on its tick'
    lone = Colorbar(scale=Scale(vmin=0.0, vmax=1.0), ticks=[5.0]).bands()
    assert lone[0] == (4.5, 5.5), 'a lone tick gets a unit-wide band rather than a zero-width one'


def test_a_continuous_bar_has_no_bands_to_name() -> None:
    """An unsupported combination raises rather than answering with a made-up edge."""
    with pytest.raises(ValueError, match='continuous Colorbar has no bands'):
        Colorbar(scale=Scale(vmin=0.0, vmax=1.0)).bands()


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
