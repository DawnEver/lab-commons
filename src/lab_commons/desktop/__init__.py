"""One door to a host's windows: list them, capture them, read their controls, press a labelled one.

Facts and semantic acts only, vendor- and agent-blind: which window means a stall, and which button a
dialog needs, is the CALLER's policy. Every act names the pids it may touch (:class:`NotOwnedError`
otherwise), resolves an EXACT label once and is never retried; there are no coordinate clicks or
synthetic keys. A host that cannot do a verb refuses it by name (:class:`UnsupportedError`, ``reason``).
Backends: Win32 + UI Automation (ctypes COM), X11 (``xprop``/``xwininfo``/``xwd``), macOS (CoreGraphics,
``screencapture``, System Events). CPU seconds, to tell busy from hung: :func:`lab_commons.proccpu.cpu_seconds`.
CLI: ``python -m lab_commons.desktop windows|capture|controls|press|set-value|close [--json]``.
"""

from __future__ import annotations

from lab_commons.desktop._core import (
    REASONS,
    Backend,
    Control,
    ControlDisabledError,
    DesktopError,
    LabelAmbiguousError,
    LabelNotFoundError,
    NotOwnedError,
    UnsupportedError,
    Window,
    WindowBusyError,
    png_bytes,
)
from lab_commons.desktop._door import backend, capture, close, controls, press, set_value, windows

__all__ = [
    'REASONS',
    'Backend',
    'Control',
    'ControlDisabledError',
    'DesktopError',
    'LabelAmbiguousError',
    'LabelNotFoundError',
    'NotOwnedError',
    'UnsupportedError',
    'Window',
    'WindowBusyError',
    'backend',
    'capture',
    'close',
    'controls',
    'png_bytes',
    'press',
    'set_value',
    'windows',
]
