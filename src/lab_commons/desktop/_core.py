"""The platform-neutral half of :mod:`lab_commons.desktop`: the facts, the refusals, the guard, the PNG.

A backend (``_win``, ``_x11``, ``_darwin``) only reads and acts; every policy a caller relies on --
the ownership guard, the exact-label rule, the visibility and title filters -- lives here once, so a
backend cannot skip it and a test drives it through a fake.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

#: Every reason code a refusal of this package carries. A caller branches on ``error.reason``, never on text.
REASONS = frozenset(
    {
        'desktop.unsupported_platform',
        'desktop.no_x11',
        'desktop.tool_missing',
        'desktop.no_atspi',
        'desktop.no_accessibility',
        'desktop.capture_format',
        'desktop.not_closable',
        'desktop.window_busy',
        'desktop.not_owned',
        'desktop.label_not_found',
        'desktop.label_ambiguous',
        'desktop.control_disabled',
        'desktop.control_gone',
        'desktop.not_actionable',
    }
)


class DesktopError(RuntimeError):
    """A refusal by name: ``reason`` is one of :data:`REASONS`, the message says why and what to do."""

    reason = ''

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason
        if self.reason not in REASONS:
            msg = f'undeclared desktop reason {self.reason!r}'
            raise ValueError(msg)


class UnsupportedError(DesktopError):
    """Refused on this host (the platform, display server, missing tool or permission); ``reason`` says which."""

    reason = 'desktop.unsupported_platform'


class WindowBusyError(DesktopError):
    """The window's UI thread did not answer within the deadline: busy or hung, so it was not touched."""

    reason = 'desktop.window_busy'


class NotOwnedError(DesktopError):
    """The window's pid is outside the pids the caller may touch, or its handle no longer names it."""

    reason = 'desktop.not_owned'


class LabelNotFoundError(DesktopError):
    """No control carries exactly this label (and supports the act)."""

    reason = 'desktop.label_not_found'


class LabelAmbiguousError(DesktopError):
    """More than one control carries exactly this label: refused, never guessed."""

    reason = 'desktop.label_ambiguous'


class ControlDisabledError(DesktopError):
    """The one control with this label is disabled: acting on it would do nothing."""

    reason = 'desktop.control_disabled'


class ControlGoneError(DesktopError):
    """No control of the window's CURRENT tree carries this id: it was removed or rebuilt since it was read."""

    reason = 'desktop.control_gone'


class NotActionableError(DesktopError):
    """The control with this id does not support the act (a text cannot be pressed)."""

    reason = 'desktop.not_actionable'


@dataclass(frozen=True)
class Window:
    """One top-level window. ``rect`` is ``(left, top, right, bottom)`` in physical screen pixels.

    ``enabled`` False means a modal dialog owns its input; ``responding`` False means its UI thread missed
    the probe (busy or hung). ``None`` in either is the platform not saying -- never a guessed ``True``.
    ``class_name`` is the window class (Win32 ``GetClassName``; ``''`` where the platform has none) and
    ``owner`` the handle of the window owning it (Win32 ``GW_OWNER``: a dialog's main window; ``0`` for none),
    ``None`` where the platform does not say.
    """

    handle: int
    pid: int
    title: str
    rect: tuple[int, int, int, int]
    visible: bool
    enabled: bool | None
    responding: bool | None
    class_name: str = ''
    owner: int | None = None


@dataclass(frozen=True)
class Control:
    """One element of a window's accessibility tree; ``actions``: the acts it supports (``press``, ``set_value``).

    ``id`` names the element while it lives -- the UIA RuntimeId on Windows, ``<bus>:<object path>`` on AT-SPI,
    the index path ``i.j.k`` below the window on macOS -- so a caller can walk the tree and act by it.
    ``parent`` is the parent's id, ``None`` for a child of the window itself. ``class_name`` (UIA ``ClassName``),
    ``automation_id`` (UIA ``AutomationId``, AT-SPI ``AccessibleId``, AX ``AXIdentifier``) and ``help_text`` are
    ``''`` / ``None`` where the platform has none; ``offscreen`` is ``None`` where it does not say.
    """

    role: str
    name: str
    value: str | None
    enabled: bool
    actions: tuple[str, ...] = ()
    id: str = ''
    parent: str | None = None
    class_name: str = ''
    automation_id: str | None = None
    help_text: str = ''
    offscreen: bool | None = None


class Backend(Protocol):
    """What one platform provides; every method reads or acts on the live desktop, and nothing else."""

    def windows(self) -> list[Window]: ...

    def capture(self, window: Window, timeout_s: float) -> bytes: ...

    def controls(self, window: Window) -> list[Control]: ...

    def press(self, window: Window, label: str | None, id: str | None) -> None: ...  # noqa: A002 -- Control.id

    def set_value(self, window: Window, label: str | None, id: str | None, text: str) -> None: ...  # noqa: A002

    def close(self, window: Window) -> None: ...


T = TypeVar('T')


def pick[T](
    window: Window,
    act: str,
    candidates: Sequence[tuple[Control, T]],
    *,
    label: str | None = None,
    id: str | None = None,  # noqa: A002 -- Control.id
) -> T:
    """The reference of the ONE control *act* targets, by exact *label* or by *id*; refused by name otherwise.

    A backend passes EVERY control it read, with its reference. By label only the controls supporting *act*
    count; by id, an id absent from the tree is :class:`ControlGoneError`, one without *act*
    :class:`NotActionableError`.
    """
    where = f'window {window.handle:#x} ({window.title!r}, pid {window.pid})'
    if id is not None:
        named = [(c, ref) for c, ref in candidates if c.id == id]
        if not named:
            msg = f'no control with id {id!r} in the current tree of {where}: read `controls` again'
            raise ControlGoneError(msg)
        if act not in named[0][0].actions:
            msg = f'the control with id {id!r} in {where} does not support {act!r}'
            raise NotActionableError(msg)
        what = f'id {id!r}'
    else:
        named = [(c, ref) for c, ref in candidates if act in c.actions and c.name == label]
        if not named:
            msg = f'no actionable control labelled exactly {label!r} in {where}; read `controls` for the labels it has'
            raise LabelNotFoundError(msg)
        if len(named) > 1:
            msg = f'{len(named)} controls labelled exactly {label!r} in {where}: refused rather than guessed'
            raise LabelAmbiguousError(msg)
        what = f'label {label!r}'
    control, ref = named[0]
    if not control.enabled:
        msg = f'the control with {what} in {where} is disabled'
        raise ControlDisabledError(msg)
    return ref


def one_target(label: str | None, id: str | None) -> None:  # noqa: A002 -- Control.id
    """Refuse a call naming neither or both of *label* and *id*: a caller's error, not a desktop refusal."""
    if (label is None) == (id is None):
        msg = 'name exactly one of label= and id='
        raise TypeError(msg)


def png_bytes(width: int, height: int, bgra: bytes) -> bytes:
    """Encode top-down 32-bit BGRA pixels as an RGB PNG (stdlib only: zlib + struct)."""
    stride = width * 4
    if len(bgra) < stride * height:
        msg = f'{width}x{height} BGRA needs {stride * height} bytes, got {len(bgra)}'
        raise ValueError(msg)
    rows = bytearray()
    for y in range(height):
        line = bgra[y * stride : (y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        rows += b'\x00' + rgb

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', header)
        + chunk(b'IDAT', zlib.compress(bytes(rows), 6))
        + chunk(b'IEND', b'')
    )
