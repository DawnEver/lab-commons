"""Explicit-font glyph metrics in caller drawing units, for sizing node boxes to their text; no font substitution."""

from __future__ import annotations

import math
from pathlib import Path

__all__ = ['FontMetrics']


class FontMetrics:
    """One layout-owned FreeType face; callers must not share it across threads."""

    def __init__(self, filename: str | Path) -> None:
        """Load the face at ``filename``; refused unless it is an existing file."""
        from matplotlib.ft2font import FT2Font, LoadFlags  # noqa: PLC0415 -- lazy: matplotlib is the `viz` extra, paid only by a caller that measures text.

        path = Path(filename).resolve(strict=True)
        if not path.is_file():
            message = 'expected an explicit font file'
            raise ValueError(message)
        self._font = FT2Font(str(path))
        self._flags = LoadFlags.NO_HINTING
        self.family_name = self._font.family_name
        self.style_name = self._font.style_name
        # Match Matplotlib's public text metric conversion: 100 pt at 72 dpi,
        # FreeType 26.6 subpixels, then scale to the requested drawing size.
        self._font.set_size(100, 72)

    def extent(self, text: str, size: float) -> tuple[float, float, float]:
        """``(width, height, descent)`` of ``text`` at font ``size``, in the drawing unit ``size`` is in."""
        if (
            not isinstance(text, str)
            or not text
            or isinstance(size, bool)
            or not isinstance(size, (int, float))
            or not math.isfinite(size)
            or size <= 0
        ):
            message = 'expected nonempty text and positive finite drawing font size'
            raise ValueError(message)
        if any(self._font.get_char_index(ord(char)) == 0 for char in text):
            message = 'explicit font lacks a requested glyph'
            raise ValueError(message)
        self._font.set_text(text, 0.0, flags=self._flags)
        width, height = self._font.get_width_height()
        scale = size / (64 * 100)
        return width * scale, height * scale, self._font.get_descent() * scale
