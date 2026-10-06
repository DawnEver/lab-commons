"""``lab_commons.viz.bokeh`` — the bokeh adapter, an EQUAL PEER of the matplotlib one.

BOKEH IS AN OPTIONAL EXTRA, so this module degrades rather than stranding the suite when
``viz-bokeh`` is not installed -- the module-scope ``pytest.importorskip`` shape, for the reason
``test_viz_mpl.py`` gives.

NOTHING HERE OPENS A BROWSER. ``show`` is only ever called with ``interactive=False``, and the
artifact every assertion is taken on is the saved page.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _viz_figure import draw_everything

from lab_commons.viz import Field, Renderer, Scale, Style

pytest.importorskip('bokeh')

from bokeh import palettes

from lab_commons.viz.bokeh import PALETTES, BokehRenderer


def test_the_adapter_satisfies_the_protocol() -> None:
    """The adapter is checked against the SAME contract the vocabulary declares."""
    renderer = BokehRenderer()
    assert isinstance(renderer, Renderer)
    assert renderer.style.figure_size == Style().figure_size


def test_the_shared_description_reaches_the_page(tmp_path: Path) -> None:
    """Every subject in the shared description leaves a glyph behind, and the page is written."""
    renderer = BokehRenderer()
    draw_everything(renderer)
    assert len(renderer.figure.renderers) >= 6, 'bars, lines, markers, patches, circles, segments'
    assert renderer.figure.title.text == 'every primitive'
    out = renderer.save(tmp_path / 'figure.html')
    assert out == tmp_path / 'figure.html'
    assert out is not None
    assert out.is_file()
    assert out.stat().st_size > 0
    assert '<html' in out.read_text(encoding='utf-8'), 'the artifact is not a page'


def test_a_raster_extension_is_rewritten_to_the_format_this_library_writes(tmp_path: Path) -> None:
    """A caller naming ``.png`` gets a page and the REAL path back, never a file that lies."""
    out = BokehRenderer().save(tmp_path / 'figure.png')
    assert out == tmp_path / 'figure.html'
    assert out is not None
    assert out.is_file()


def test_every_palette_name_this_adapter_carries_is_a_palette_bokeh_has() -> None:
    """Each row resolves in bokeh's own registry — family and width, read rather than assumed.

    Read off the registry instead of the module's attributes, because an attribute that does not
    exist fails at the ONE figure that used it; this fails here, for every row, with the name.
    """
    missing = sorted(
        name
        for name in PALETTES.values()
        # every width in this table is three digits; the family is what precedes them
        if int(name[-3:]) not in palettes.all_palettes.get(name[:-3], {})
    )
    assert len(PALETTES) >= 10, 'a floor: an empty table would pass this check vacuously'
    assert not missing, f'bokeh has no such palette: {missing}'


def test_a_colormap_this_adapter_cannot_draw_is_refused_by_name() -> None:
    """No silent fallback to a default: a substitution would draw a different colour scale."""
    renderer = BokehRenderer()
    field = Field(x=[0.0], y=[0.0], values=[1.0], scale=Scale(cmap='definitely-not-a-colormap'))
    with pytest.raises(ValueError, match='no bokeh palette named'):
        renderer.draw_field(field)


def test_showing_without_a_window_opens_nothing() -> None:
    """``interactive=False`` invents no path and opens no tab, which is what a headless run wants."""
    renderer = BokehRenderer()
    assert renderer.show(interactive=False) is None
    renderer.close()
