"""The public verbs of :mod:`lab_commons.desktop`: choose this host's backend, filter, guard, delegate."""

from __future__ import annotations

import sys
from collections.abc import Collection

from lab_commons.desktop import _darwin, _win, _x11
from lab_commons.desktop._core import Backend, Control, NotOwnedError, UnsupportedError, Window, one_target


def backend() -> Backend:
    """The backend of this host, chosen by ``sys.platform``; any other platform is refused by name."""
    if sys.platform == 'win32':
        return _win.Win32()
    if sys.platform == 'darwin':
        return _darwin.Darwin()
    if sys.platform.startswith('linux'):
        return _x11.X11()
    msg = f'no desktop backend for {sys.platform!r} (Windows, X11 Linux and macOS are the ones built)'
    raise UnsupportedError(msg)


def windows(
    pids: Collection[int] | None = None,
    title: str | None = None,
    *,
    hidden: bool = False,
    backend: Backend | None = None,
) -> list[Window]:
    """Top-level windows, of *pids* only when given, whose title holds *title* (case-blind); visible unless *hidden*."""
    found = _resolve(backend).windows()
    needle = title.casefold() if title else None
    return [
        w
        for w in found
        if (hidden or w.visible)
        and (pids is None or w.pid in pids)
        and (needle is None or needle in w.title.casefold())
    ]


def capture(window: Window, *, timeout_s: float = 5.0, backend: Backend | None = None) -> bytes:
    """PNG bytes of *window*; a window whose UI thread misses the probe or the deadline is :class:`WindowBusyError`."""
    return _resolve(backend).capture(window, timeout_s)


def controls(window: Window, *, backend: Backend | None = None) -> list[Control]:
    """*window*'s accessibility tree, flattened: reads a dialog's text without OCR."""
    return _resolve(backend).controls(window)


def press(
    window: Window,
    label: str | None = None,
    *,
    id: str | None = None,  # noqa: A002 -- Control.id
    owned: Collection[int],
    backend: Backend | None = None,
) -> None:
    """Press the ONE control labelled exactly *label*, or with ``Control.id`` *id*, once, never retried.

    Exactly one of *label* and *id*; *owned* are the pids it may touch. An id not in the window's tree now is
    :class:`~lab_commons.desktop.ControlGoneError`.
    """
    one_target(label, id)
    chosen = _resolve(backend)
    _guard(window, owned, chosen)
    chosen.press(window, label, id)


def set_value(
    window: Window,
    label: str | None = None,
    text: str = '',
    *,
    id: str | None = None,  # noqa: A002 -- Control.id
    owned: Collection[int],
    backend: Backend | None = None,
) -> None:
    """Set the ONE control named by *label* or *id* to *text*; *owned* and the id rule as :func:`press`."""
    one_target(label, id)
    chosen = _resolve(backend)
    _guard(window, owned, chosen)
    chosen.set_value(window, label, id, text)


def close(window: Window, *, owned: Collection[int], backend: Backend | None = None) -> None:
    """Ask *window* to close through the accessibility API (its own close, so it may still prompt)."""
    chosen = _resolve(backend)
    _guard(window, owned, chosen)
    chosen.close(window)


def _resolve(given: Backend | None) -> Backend:
    return given if given is not None else backend()


def _guard(window: Window, owned: Collection[int], chosen: Backend) -> None:
    """Refuse unless *window*'s pid is owned AND its handle still names a window of that pid now."""
    if window.pid not in owned:
        msg = f'window {window.handle:#x} ({window.title!r}) belongs to pid {window.pid}, outside {sorted(owned)}'
        raise NotOwnedError(msg)
    now = next((w for w in chosen.windows() if w.handle == window.handle), None)
    if now is None or now.pid != window.pid:
        held = 'is gone' if now is None else f'now belongs to pid {now.pid}'
        msg = f'window {window.handle:#x} ({window.title!r}) {held}: list the windows again'
        raise NotOwnedError(msg)
