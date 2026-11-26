"""The rendering edges: explicit-font text metrics for sizing boxes, and generic deterministic SVG out."""

import importlib.util
from pathlib import Path

import pytest

from lab_commons.graph_layout import Box, Point, Segment, layout_svg
from lab_commons.graph_layout.text_metrics import FontMetrics


@pytest.fixture
def metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FontMetrics:
    monkeypatch.setenv('MPLCONFIGDIR', str(tmp_path / 'mpl'))
    font = Path(importlib.util.find_spec('matplotlib').origin).parent / 'mpl-data/fonts/ttf/DejaVuSans.ttf'
    return FontMetrics(font)


def test_metrics_measure_glyphs_and_scale_in_the_requested_drawing_units(metrics) -> None:
    assert metrics.family_name == 'DejaVu Sans'
    first, second = metrics.extent('NodeLabel1', 1.3), metrics.extent('NodeLabel1', 2.6)
    assert first[0] > 2
    assert first[1] > 0
    assert second == tuple(2 * value for value in first)
    assert metrics.extent('iii', 1)[0] < metrics.extent('WWW', 1)[0]


@pytest.mark.parametrize(
    ('text', 'size'),
    [('', 1), ('x', 0), ('x', -1), ('x', True), ('x', float('nan')), ('x', float('inf')), ('\U0010ffff', 1)],
)
def test_missing_glyphs_or_invalid_sizes_refuse_instead_of_a_fallback(metrics, text, size) -> None:
    with pytest.raises(ValueError, match=r'expected|glyph'):
        metrics.extent(text, size)


_BOXES = (Box('b', 60, 20, 20, 10), Box('a', 10, 20, 20, 10))
_WIRES = {'n': (Segment(Point(20, 20), Point(50, 20)), Segment(Point(50, 20), Point(50, 20)))}


def test_svg_draws_every_box_and_wire_in_key_order_with_escaped_text() -> None:
    svg = layout_svg(100, 40, _BOXES, _WIRES, labels={'a': '<A&>'})
    assert svg.index('data-key="a"') < svg.index('data-key="b"')
    assert 'x="0" y="15" width="20" height="10"' in svg
    assert 'd="M20,20 L50,20"' in svg
    assert '&lt;A&amp;&gt;' in svg
    assert '<A&>' not in svg
    assert svg == layout_svg(100, 40, reversed(_BOXES), _WIRES, labels={'a': '<A&>'})


def test_svg_refuses_a_non_finite_coordinate_by_name() -> None:
    with pytest.raises(ValueError, match='layout_svg: a coordinate is not finite'):
        layout_svg(float('nan'), 40, ())
