"""Compound regions keep transparent holes and every existing patch option."""

import numpy as np
import pytest

from lab_commons.viz import Patch, Scale

pytest.importorskip('matplotlib')
pytest.importorskip('bokeh')

import matplotlib as mpl
from bokeh.models import MultiPolygons
from matplotlib.path import Path

from lab_commons.viz.bokeh import BokehRenderer
from lab_commons.viz.mpl import MplRenderer

mpl.use('Agg')

_OUTER = np.array([(0, 0), (10, 0), (10, 10), (0, 10)])
_HOLES = (
    np.array([(1, 1), (3, 1), (3, 3), (1, 3)]),
    np.array([(6, 6), (9, 6), (9, 9), (6, 9)]),
)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('valued', [False, True])
def test_mpl_keeps_opposite_ring_winding_and_patch_options(*, reverse: bool, valued: bool) -> None:
    outer = _OUTER[::-1] if reverse else _OUTER
    holes = tuple(ring[::-1] if reverse else ring for ring in _HOLES)
    renderer = MplRenderer()
    frame = renderer.frame()
    try:
        frame.draw_patches(
            [Patch(outer, 2.5 if valued else None, 'red', 'blue', 'region', 0.4, '/', holes)],
            Scale(vmin=0, vmax=5) if valued else None,
        )
        if valued:
            artist = frame.axes.collections[0]
            path = artist.get_paths()[0]
            assert artist.get_array().tolist() == [2.5]
        else:
            artist = frame.axes.patches[0]
            path = artist.get_path()
            assert artist.get_label() == 'region'
            assert artist.get_hatch() == '/'
            assert artist.get_facecolor() == (1.0, 0.0, 0.0, 0.4)
            assert artist.get_edgecolor() == (0.0, 0.0, 1.0, 0.4)
        assert artist.get_alpha() == 0.4
        starts = np.flatnonzero(path.codes == Path.MOVETO)
        assert len(starts) == 3
        rings = np.split(path.vertices, starts[1:])
        areas = [np.sum(ring[:-1, 0] * ring[1:, 1] - ring[1:, 0] * ring[:-1, 1]) for ring in rings]
        assert areas == [200, -8, -18]
        assert np.count_nonzero(path.codes == Path.CLOSEPOLY) == 3
    finally:
        renderer.close()


@pytest.mark.parametrize('valued', [False, True])
def test_bokeh_keeps_all_rings_in_one_region_and_patch_options(*, valued: bool) -> None:
    frame = BokehRenderer().frame()
    frame.draw_patches(
        [Patch(_OUTER, 2.5 if valued else None, 'red', 'blue', 'region', 0.4, '/', _HOLES)],
        Scale(vmin=0, vmax=5) if valued else None,
    )
    artist = frame.figure.renderers[0]
    assert isinstance(artist.glyph, MultiPolygons)
    assert artist.data_source.data['xs'] == [[[_OUTER[:, 0].tolist(), *[ring[:, 0].tolist() for ring in _HOLES]]]]
    assert artist.data_source.data['ys'] == [[[_OUTER[:, 1].tolist(), *[ring[:, 1].tolist() for ring in _HOLES]]]]
    assert artist.glyph.fill_alpha == 0.4
    if valued:
        assert artist.data_source.data['value'] == [2.5]
        assert artist.glyph.fill_color.transform.low == 0
        assert artist.glyph.fill_color.transform.high == 5
    else:
        assert artist.glyph.fill_color == 'red'
        assert artist.glyph.line_color == 'blue'
        assert artist.glyph.line_alpha == 0.4
        assert artist.glyph.hatch_pattern == '/'


def test_mpl_holes_reveal_underlying_regions_and_preserve_an_independent_island() -> None:
    renderer = MplRenderer()
    frame = renderer.frame()
    try:
        frame.draw_patches(
            [
                Patch(_OUTER, color='blue'),
                Patch(_OUTER, color='red', holes=_HOLES),
                Patch([(1.5, 1.5), (2.5, 1.5), (2.5, 2.5), (1.5, 2.5)], color='lime'),
            ]
        )
        frame.axes.set_xlim(0, 10)
        frame.axes.set_ylim(0, 10)
        renderer.figure.canvas.draw()
        pixels = np.asarray(renderer.figure.canvas.buffer_rgba())
        for point, expected in (
            ((5, 5), [255, 0, 0]),
            ((1.25, 2), [0, 0, 255]),
            ((7, 7), [0, 0, 255]),
            ((2, 2), [0, 255, 0]),
        ):
            x, y = frame.axes.transData.transform(point).astype(int)
            assert pixels[pixels.shape[0] - 1 - y, x, :3].tolist() == expected
    finally:
        renderer.close()
