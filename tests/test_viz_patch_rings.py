"""Patch ring data has one NumPy-only coercion and numeric-shape boundary."""

import numpy as np
import pytest

from lab_commons.viz import Patch

_OUTER = [(0, 0), (4, 0), (4, 4), (0, 4)]
_HOLE = [(1, 1), (2, 1), (1, 2)]
_INVALID = (
    [],
    [0, 1],
    [(0, 0), (1, 0)],
    [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
    [(0, 0), (1,), (0, 1)],
    [(0, 0), (1, np.nan), (0, 1)],
    [(0, 0), (1, np.inf), (0, 1)],
    [(0, 0), (1, -np.inf), (0, 1)],
    [('not-a-coordinate', 0), (1, 0), (0, 1)],
)


def test_patch_rings_preserve_boundary_order_coordinates_and_input_data() -> None:
    patch = Patch(_OUTER, holes=(_HOLE,))
    rings = patch.rings()
    assert isinstance(rings, tuple)
    assert len(rings) == 2
    for actual, expected in zip(rings, (_OUTER, _HOLE), strict=True):
        assert actual.dtype == np.dtype(float)
        np.testing.assert_array_equal(actual, expected)
    assert patch.vertices is _OUTER
    assert patch.holes == (_HOLE,)


@pytest.mark.parametrize('boundary', _INVALID)
@pytest.mark.parametrize('index', [0, 1])
def test_malformed_exterior_and_hole_data_share_one_explicit_refusal(boundary, index: int) -> None:
    patch = Patch(boundary) if index == 0 else Patch(_OUTER, holes=(boundary,))
    with pytest.raises(ValueError, match=rf'patch boundary {index} must be a finite'):
        patch.rings()


def test_reusing_a_mutated_description_revalidates_data_instead_of_caching_old_rings() -> None:
    outer = np.asarray(_OUTER, dtype=float)
    patch = Patch(outer)
    assert len(patch.rings()) == 1
    outer[0, 0] = np.nan
    with pytest.raises(ValueError, match='patch boundary 0 must be a finite'):
        patch.rings()
