"""``lab_commons.viz.mpl`` — the matplotlib adapter, and the family's whole display policy.

MATPLOTLIB IS AN OPTIONAL EXTRA, so this module DEGRADES rather than stranding the suite when the
``viz-mpl`` extra is not installed: a module-scope ``pytest.importorskip`` leaves the file
collectable, which is the shape the kit's own collection census classifies as a DEGRADE rather than
as an error. It is deliberately not a ``skip`` mark, because a mark is not a guard -- the module body
runs before pytest can read one.

NO WINDOW IS EVER OPENED HERE, and Agg is selected for exactly that reason before any figure is
built -- a suite that pops a window on somebody's desktop is a suite that gets run nowhere. It is
also what makes the degrade path testable rather than assumed: under Agg a show CANNOT happen, and
every assertion below is about what happens instead.

THE GUARD IS A BARE ``pytest.importorskip`` CALL rather than a binding, and that is deliberate: this
suite reads its own tree for the shape it recommends, and a bare guard is the one that leaves the
imports after it at module scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _viz_figure import draw_everything

from lab_commons.viz import Renderer, Series, Style

pytest.importorskip('matplotlib')

import matplotlib as mpl

from lab_commons.viz import mpl as viz_mpl

mpl.use('Agg')


def test_the_adapter_satisfies_the_protocol() -> None:
    """The adapter is checked against the SAME contract the vocabulary declares."""
    renderer = viz_mpl.MplRenderer()
    assert isinstance(renderer, Renderer)
    assert renderer.figure.get_size_inches().tolist() == [8.0, 6.0]
    renderer.close()


def test_the_shared_description_reaches_the_canvas() -> None:
    """Every subject in the shared description leaves an artist behind — measured on the figure.

    A count of artists is the honest reading here: a verb that returned without drawing anything
    would satisfy the protocol and produce an empty picture, which no signature can catch.
    """
    renderer = viz_mpl.MplRenderer()
    draw_everything(renderer)
    axes = renderer.figure.axes[0]
    assert len(axes.lines) >= 4, 'the chart, the waveform traces and the wire are lines'
    assert len(axes.patches) >= 3, 'two winding patches and a coil-side circle'
    assert len(axes.texts) >= 1, 'the label'
    assert axes.collections, 'the field map is a collection'
    assert len(renderer.figure.axes) == 2, 'the field map drew no colour bar'
    renderer.close()


def test_saving_writes_a_file_and_opens_no_window(tmp_path: Path) -> None:
    """The batch tail: a path back, a non-empty file, and no window however headless the box."""
    renderer = viz_mpl.MplRenderer()
    draw_everything(renderer)
    out = renderer.save(tmp_path / 'figure.png')
    assert out == tmp_path / 'figure.png'
    assert out is not None
    assert out.is_file()
    assert out.stat().st_size > 0


def test_one_description_takes_the_same_colours_every_time() -> None:
    """The palette position is a function of one figure, not of what an earlier figure did."""
    first, second = viz_mpl.MplRenderer(), viz_mpl.MplRenderer()
    for renderer in (first, second):
        renderer.draw_line(Series(x=[0.0, 1.0], y=[0.0, 1.0]))
    assert first.figure.axes[0].lines[0].get_color() == Style().palette[0]
    assert second.figure.axes[0].lines[0].get_color() == Style().palette[0]
    first.close()
    second.close()


def test_a_noninteractive_backend_is_never_talked_into_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process owner's declaration wins: a harness that sets Agg must not get a Tk window.

    MEASURED in the tree this policy was ported from -- a light test run opened a live window on the
    user's desktop because the probe ignored the declared backend.
    """
    monkeypatch.setenv('MPLBACKEND', 'Agg')
    assert viz_mpl.enable_interactive_backend() is False
    assert viz_mpl.is_interactive_backend() is False


def test_saving_and_showing_are_not_alternatives(tmp_path: Path) -> None:
    """A window asked for and unavailable must NOT discard the file.

    The bug this pins: a version of the display tail returned from the show branch before writing,
    so asking for a window produced no output at all -- in a workflow where the figure IS an output.
    """
    figure = viz_mpl.MplRenderer().figure
    out = viz_mpl.show_or_save(figure, tmp_path / 'shown.png', interactive=True)
    assert out == tmp_path / 'shown.png'
    assert out is not None
    assert out.is_file()
    assert viz_mpl.is_interactive_backend() is False, 'this test is only meaningful with no window available'


def test_a_show_with_nothing_to_show_returns_no_path() -> None:
    """``None`` means "there was nothing to write", and a dead canvas is not an exception."""
    assert viz_mpl.show_or_save(viz_mpl.MplRenderer().figure, None, interactive=True) is None


def test_apply_style_is_the_one_place_rcparams_are_set() -> None:
    """The style object maps onto matplotlib's globals in ONE function, and it is reversible."""
    before = dict(mpl.rcParams)
    try:
        viz_mpl.apply_style(Style(font_size=18.0, figure_size=(4.0, 3.0)))
        assert mpl.rcParams['font.size'] == 18.0
        assert list(mpl.rcParams['figure.figsize']) == [4.0, 3.0]
        viz_mpl.apply_style()
        assert mpl.rcParams['font.size'] == Style().font_size
    finally:
        mpl.rcParams.update(before)
