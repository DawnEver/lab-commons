"""The Windows backend: Win32 for windows and pixels, UI Automation through ctypes COM for controls and acts.

Importable on every OS (nothing touches ``ctypes.windll`` until :class:`Win32` is built), so its pure
tables are unit-tested anywhere. Every call into another process's UI thread is bounded twice: a
``WM_NULL`` probe with ``SMTO_ABORTIFHUNG`` first (a hung window fails it at once), then the work itself
on a daemon thread with a deadline -- ``PrintWindow`` and UIA are answered by the target's own UI
thread, and one busy in a solve never answers (measured 2026-11-12 on a running PLECS). A thread that
misses the deadline is abandoned with its handles, never freed under a call still using them.

Rects and captures are PHYSICAL pixels: each reading thread runs per-monitor-v2 DPI aware, as the
measured SIMBA capture did, so a 150 % display neither crops nor scales the picture.
"""

from __future__ import annotations

import ctypes
import struct
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, TypeVar

from lab_commons.desktop._core import (
    Control,
    NotOwnedError,
    UnsupportedError,
    Window,
    WindowBusyError,
    pick,
    png_bytes,
)

__all__ = ['ROLES', 'Win32', 'bitmap_info']

#: ``PrintWindow``: render DirectComposition / GPU content too (Qt and WPF windows render black without it).
_PW_RENDERFULLCONTENT = 0x2
_SMTO_ABORTIFHUNG = 0x2
#: The responsiveness probe's deadline, milliseconds.
_PROBE_MS = 2000
#: ``DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2``.
_PER_MONITOR_V2 = -4

#: UIA control type id -> role. Unknown ids read ``uia:<id>``, never a guessed role.
ROLES = {
    50000 + i: name
    for i, name in enumerate(
        (
            'button',
            'calendar',
            'checkbox',
            'combobox',
            'edit',
            'hyperlink',
            'image',
            'listitem',
            'list',
            'menu',
            'menubar',
            'menuitem',
            'progressbar',
            'radiobutton',
            'scrollbar',
            'slider',
            'spinner',
            'statusbar',
            'tab',
            'tabitem',
            'text',
            'toolbar',
            'tooltip',
            'tree',
            'treeitem',
            'custom',
            'group',
            'thumb',
            'datagrid',
            'dataitem',
            'document',
            'splitbutton',
            'window',
            'pane',
            'header',
            'headeritem',
            'table',
            'titlebar',
            'separator',
        )
    )
}

# UI Automation, from UIAutomationClient.h. Vtable slots count IUnknown's three first.
_CLSID_CUIAutomation = '{ff48dba4-60ef-4201-aa87-54103eef594e}'
_IID_IUIAutomation = '{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}'
_IID_INVOKE = '{fb377fbe-8ea6-46d5-9c73-6499642d3059}'
_IID_VALUE = '{a94cd8b1-0844-4cd6-9d2d-640537ab39e9}'
_IID_WINDOW = '{0faef453-9208-43ef-bbb2-3b485177864f}'
_PATTERN_INVOKE, _PATTERN_VALUE, _PATTERN_WINDOW = 10000, 10002, 10009
_TREESCOPE_CHILDREN = 2
# IUIAutomation
_ELEMENT_FROM_HANDLE, _CREATE_TRUE_CONDITION = 6, 21
# IUIAutomationElement
_RUNTIME_ID, _FIND_ALL, _PATTERN_AS, _PROCESS_ID, _CONTROL_TYPE, _NAME, _IS_ENABLED = 4, 6, 14, 20, 21, 23, 28
_AUTOMATION_ID, _CLASS_NAME, _HELP_TEXT, _IS_OFFSCREEN = 29, 30, 31, 38
#: ``GetWindow``: the owner of a window; ``StretchBlt``'s smoothing mode; its plain copy.
_GW_OWNER, _HALFTONE, _SRCCOPY = 4, 4, 0x00CC0020
# IUIAutomationElementArray
_LENGTH, _ELEMENT = 3, 4
# InvokePattern.Invoke, ValuePattern.SetValue / get_CurrentValue / get_CurrentIsReadOnly, WindowPattern.Close
_INVOKE, _SET_VALUE, _CURRENT_VALUE, _IS_READ_ONLY, _CLOSE = 3, 3, 4, 5, 3

T = TypeVar('T')


def bitmap_info(width: int, height: int) -> bytes:
    """A ``BITMAPINFOHEADER`` asking ``GetDIBits`` for top-down (negative height) 32-bit ``BI_RGB`` rows."""
    return struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)


def _guid(text: str) -> Any:  # noqa: ANN401 -- a ctypes GUID structure
    guid = (ctypes.c_byte * 16)()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


class _Com:
    """One COM interface pointer; ``call`` invokes a vtable slot, raising ``OSError`` on a failed HRESULT."""

    def __init__(self, pointer: int | None) -> None:
        self.pointer = pointer

    def __bool__(self) -> bool:
        return bool(self.pointer)

    def call(self, slot: int, *args: tuple[Any, Any]) -> None:
        vtable = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        function = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *(kind for kind, _ in args))(vtable[slot])
        function(self.pointer, *(value for _, value in args))

    def out(self, slot: int, *args: tuple[Any, Any]) -> _Com:
        result = ctypes.c_void_p()
        self.call(slot, *args, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(result)))
        return _Com(result.value)

    def read(self, slot: int, kind: Any) -> Any:  # noqa: ANN401 -- the C value of a property getter
        result = kind()
        self.call(slot, (ctypes.POINTER(kind), ctypes.byref(result)))
        return result.value

    def text(self, slot: int) -> str:
        result = ctypes.c_void_p()
        self.call(slot, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(result)))
        if not result.value:
            return ''
        try:
            return ctypes.wstring_at(result.value)
        finally:
            ctypes.windll.oleaut32.SysFreeString(ctypes.c_void_p(result.value))

    def release(self) -> None:
        if self.pointer:
            vtable = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(self.pointer)
            self.pointer = None


class Win32:
    """The live Windows desktop; built only on Windows (``ctypes.windll``)."""

    def __init__(self) -> None:
        if not hasattr(ctypes, 'windll'):
            msg = 'the Win32 desktop backend needs Windows'
            raise UnsupportedError(msg)
        wt = wintypes
        self._user32 = user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._gdi32 = gdi32 = ctypes.WinDLL('gdi32')
        user32.SendMessageTimeoutW.argtypes = (
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)
        )  # fmt: skip
        user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
        for name in ('IsWindowVisible', 'IsWindowEnabled', 'GetWindowTextLengthW', 'IsWindow'):
            getattr(user32, name).argtypes = (wt.HWND,)
        user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
        user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
        user32.GetWindow.restype = wt.HWND
        user32.GetWindow.argtypes = (wt.HWND, wt.UINT)
        gdi32.StretchBlt.argtypes = (wt.HDC, *(ctypes.c_int,) * 4, wt.HDC, *(ctypes.c_int,) * 4, wt.DWORD)
        gdi32.SetStretchBltMode.argtypes = (wt.HDC, ctypes.c_int)
        self._window_dpi = getattr(user32, 'GetWindowDpiAwarenessContext', None)
        if self._window_dpi is not None:
            self._window_dpi.restype = ctypes.c_void_p
            self._window_dpi.argtypes = (wt.HWND,)
        user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
        user32.GetDC.restype = wt.HDC
        user32.GetDC.argtypes = (wt.HWND,)
        user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
        user32.PrintWindow.argtypes = (wt.HWND, wt.HDC, wt.UINT)
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateCompatibleDC.argtypes = (wt.HDC,)
        gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int)
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.SelectObject.argtypes = (wt.HDC, wt.HGDIOBJ)
        gdi32.GetDIBits.argtypes = (wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT)
        gdi32.DeleteObject.argtypes = (wt.HGDIOBJ,)
        gdi32.DeleteDC.argtypes = (wt.HDC,)
        self._set_dpi = getattr(user32, 'SetThreadDpiAwarenessContext', None)
        if self._set_dpi is not None:
            self._set_dpi.restype = ctypes.c_void_p
            self._set_dpi.argtypes = (ctypes.c_void_p,)

    # ---- facts -------------------------------------------------------------------------------------------

    def windows(self) -> list[Window]:
        """Every top-level window; only visible ones are probed (``responding`` is ``None`` on a hidden one)."""
        wt, user32 = wintypes, self._user32
        handles: list[int] = []
        proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        visit = proto(lambda hwnd, _lparam: handles.append(int(hwnd or 0)) or True)
        previous = self._dpi_aware()
        try:
            user32.EnumWindows(visit, 0)
            return [self._window(hwnd) for hwnd in handles]
        finally:
            self._dpi_restore(previous)

    def _window(self, hwnd: int) -> Window:
        wt, user32 = wintypes, self._user32
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        visible = bool(user32.IsWindowVisible(hwnd))
        kind = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, kind, len(kind))
        return Window(
            class_name=kind.value,
            owner=int(user32.GetWindow(hwnd, _GW_OWNER) or 0),
            handle=hwnd,
            pid=int(pid.value),
            title=buffer.value,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            visible=visible,
            enabled=bool(user32.IsWindowEnabled(hwnd)),
            responding=self._responds(hwnd) if visible else None,
        )

    def _responds(self, hwnd: int) -> bool:
        """WM_NULL with SMTO_ABORTIFHUNG: a hung window fails at once, a busy one after the probe deadline."""
        result = ctypes.c_size_t()
        return bool(self._user32.SendMessageTimeoutW(hwnd, 0, 0, 0, _SMTO_ABORTIFHUNG, _PROBE_MS, ctypes.byref(result)))

    def _ready(self, window: Window) -> None:
        if not self._user32.IsWindow(window.handle) or not self._responds(window.handle):
            msg = f'window {window.handle:#x} ({window.title!r}) is gone or not responding: not touched'
            raise WindowBusyError(msg)

    # ---- pixels ------------------------------------------------------------------------------------------

    def capture(self, window: Window, timeout_s: float) -> bytes:
        self._ready(window)
        return _deadline(window, timeout_s, lambda: self._render(window), com=False)

    def _size(self, window: Window) -> tuple[int, int]:
        rect = wintypes.RECT()
        self._user32.GetWindowRect(window.handle, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            msg = f'window {window.handle:#x} ({window.title!r}) has no area: {width}x{height}'
            raise WindowBusyError(msg)
        return width, height

    def _render(self, window: Window) -> bytes:
        """``PrintWindow`` in the WINDOW's own DPI awareness, scaled to its physical size.

        ``PrintWindow`` draws a window at the size its own awareness sees: a DPI-unaware window on a 175 %
        display draws its logical 57 % into the top-left of a physical-size bitmap and leaves the rest black
        (measured 2026-11-14 on FEMM). So this thread adopts the window's awareness to read its size and
        render it, then reads the physical size per-monitor-aware and stretches the render to it; the PNG is
        always ``Window.rect``'s size. This thread is ours alone: its awareness is never restored.
        """
        user32, gdi32 = self._user32, self._gdi32
        own = self._window_dpi(window.handle) if self._window_dpi is not None else None
        if own and self._set_dpi is not None:
            self._set_dpi(ctypes.c_void_p(own))
        else:
            self._dpi_aware()
        width, height = self._size(window)
        screen = user32.GetDC(None)
        made: list[tuple[int, int, int]] = []  # (dc, bitmap, previous) to free

        def surface(w: int, h: int) -> int:
            dc = gdi32.CreateCompatibleDC(screen)
            bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
            made.append((dc, bitmap, gdi32.SelectObject(dc, bitmap)))
            return dc

        try:
            drawn = surface(width, height)
            if not user32.PrintWindow(window.handle, drawn, _PW_RENDERFULLCONTENT):
                msg = f'PrintWindow refused window {window.handle:#x} ({window.title!r})'
                raise WindowBusyError(msg)
            self._dpi_aware()
            physical = self._size(window)
            if physical != (width, height):
                scaled = surface(*physical)
                gdi32.SetStretchBltMode(scaled, _HALFTONE)
                gdi32.StretchBlt(scaled, 0, 0, *physical, drawn, 0, 0, width, height, _SRCCOPY)
            final_dc, final_bitmap, _ = made[-1]
            width, height = physical
            header = ctypes.create_string_buffer(bitmap_info(width, height), 40 + 12)
            pixels = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(final_dc, final_bitmap, 0, height, pixels, header, 0):
                msg = f'GetDIBits read nothing from window {window.handle:#x}'
                raise WindowBusyError(msg)
            return png_bytes(width, height, pixels.raw)
        finally:
            for dc, bitmap, previous in made:
                gdi32.SelectObject(dc, previous)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)

    # ---- UI Automation -----------------------------------------------------------------------------------

    def controls(self, window: Window) -> list[Control]:
        self._ready(window)
        return _deadline(window, _UIA_S, lambda: _walk(window, lambda found: [c for c, _ in found]), com=True)

    def press(self, window: Window, label: str | None, id: str | None) -> None:  # noqa: A002 -- Control.id
        self._ready(window)

        def act(found: list[tuple[Control, _Com]]) -> None:
            element = pick(window, 'press', found, label=label, id=id)
            _with_pattern(element, _PATTERN_INVOKE, _IID_INVOKE, lambda p: p.call(_INVOKE))

        _deadline(window, _UIA_S, lambda: _walk(window, act), com=True)

    def set_value(self, window: Window, label: str | None, id: str | None, text: str) -> None:  # noqa: A002
        self._ready(window)

        def act(found: list[tuple[Control, _Com]]) -> None:
            element = pick(window, 'set_value', found, label=label, id=id)
            value = ctypes.windll.oleaut32.SysAllocString
            value.restype = ctypes.c_void_p
            bstr = value(ctypes.c_wchar_p(text))
            try:
                _with_pattern(
                    element, _PATTERN_VALUE, _IID_VALUE, lambda p: p.call(_SET_VALUE, (ctypes.c_void_p, bstr))
                )
            finally:
                ctypes.windll.oleaut32.SysFreeString(ctypes.c_void_p(bstr))

        _deadline(window, _UIA_S, lambda: _walk(window, act), com=True)

    def close(self, window: Window) -> None:
        self._ready(window)

        def act(_automation: _Com, root: _Com) -> None:
            pattern = root.out(
                _PATTERN_AS, (ctypes.c_int, _PATTERN_WINDOW), (ctypes.c_void_p, ctypes.byref(_guid(_IID_WINDOW)))
            )
            if not pattern:
                msg = f'window {window.handle:#x} ({window.title!r}) exposes no UIA WindowPattern to close it'
                raise UnsupportedError(msg, 'desktop.not_closable')
            try:
                pattern.call(_CLOSE)
            finally:
                pattern.release()

        _deadline(window, _UIA_S, lambda: _root(window, act), com=True)

    # ---- DPI ---------------------------------------------------------------------------------------------

    def _dpi_aware(self) -> int | None:
        return self._set_dpi(ctypes.c_void_p(_PER_MONITOR_V2)) if self._set_dpi is not None else None

    def _dpi_restore(self, previous: int | None) -> None:
        if self._set_dpi is not None and previous:
            self._set_dpi(ctypes.c_void_p(previous))


#: The deadline on one UIA walk or act: the probe already passed, so this bounds a slow, not a hung, window.
_UIA_S = 10.0


def _deadline[T](window: Window, timeout_s: float, work: Callable[[], T], *, com: bool) -> T:
    """Run *work* on a daemon thread (its own COM apartment when *com*); past *timeout_s* it is busy."""
    outcome: list[tuple[bool, Any]] = []

    def run() -> None:
        if com:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        try:
            outcome.append((True, work()))
        except BaseException as failed:  # noqa: BLE001 -- carried to the caller's thread and re-raised there
            outcome.append((False, failed))
        finally:
            if com:
                ctypes.windll.ole32.CoUninitialize()

    worker = threading.Thread(target=run, daemon=True, name=f'desktop-{window.handle:#x}')
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        msg = (
            f'window {window.handle:#x} ({window.title!r}) did not answer within {timeout_s:g} s: its UI thread is busy'
        )
        raise WindowBusyError(msg)
    ok, value = outcome[0]
    if not ok:
        raise value
    return value


def _root[T](window: Window, use: Callable[[_Com, _Com], T]) -> T:
    """Bind UIA to *window*'s handle, check it is still a window of that pid, then ``use(automation, root)``."""
    pointer = ctypes.c_void_p()
    ctypes.oledll.ole32.CoCreateInstance(
        ctypes.byref(_guid(_CLSID_CUIAutomation)),
        None,
        1,
        ctypes.byref(_guid(_IID_IUIAutomation)),
        ctypes.byref(pointer),
    )  # 1: CLSCTX_INPROC_SERVER
    automation = _Com(pointer.value)
    try:
        root = automation.out(_ELEMENT_FROM_HANDLE, (ctypes.c_void_p, window.handle))
        try:
            if root.read(_PROCESS_ID, ctypes.c_int) != window.pid:
                msg = f'window {window.handle:#x} is no longer a window of pid {window.pid}'
                raise NotOwnedError(msg)
            return use(automation, root)
        finally:
            root.release()
    finally:
        automation.release()


def _walk[T](window: Window, use: Callable[[list[tuple[Control, _Com]]], T]) -> T:
    """Every descendant OF THE WINDOW'S OWN PID, depth first, each with its parent's id, then *use*.

    A child of a foreign pid is neither listed nor acted on, and neither is its subtree.
    """

    def descend(automation: _Com, root: _Com) -> T:
        held: list[_Com] = []
        found: list[tuple[Control, _Com]] = []
        try:
            condition = automation.out(_CREATE_TRUE_CONDITION)
            held.append(condition)

            def visit(parent: _Com, parent_id: str | None) -> None:
                scope = (ctypes.c_int, _TREESCOPE_CHILDREN)
                array = parent.out(_FIND_ALL, scope, (ctypes.c_void_p, condition.pointer))
                held.append(array)
                for index in range(array.read(_LENGTH, ctypes.c_int) if array else 0):
                    element = array.out(_ELEMENT, (ctypes.c_int, index))
                    held.append(element)
                    if element.read(_PROCESS_ID, ctypes.c_int) != window.pid:
                        continue
                    control = _control(element, parent_id)
                    found.append((control, element))
                    visit(element, control.id)

            visit(root, None)
            return use(found)
        finally:
            for pointer in held:
                pointer.release()

    return _root(window, descend)


def runtime_id(numbers: list[int]) -> str:
    """A UIA RuntimeId as one ``Control.id``: its integers joined by dots."""
    return '.'.join(str(n) for n in numbers)


def _runtime_id(element: _Com) -> str:
    """``IUIAutomationElement::GetRuntimeId``: a SAFEARRAY of int, read and destroyed here."""
    oleaut32 = ctypes.windll.oleaut32
    array = ctypes.c_void_p()
    element.call(_RUNTIME_ID, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(array)))
    if not array.value:
        return ''
    try:
        low, high = ctypes.c_long(), ctypes.c_long()
        oleaut32.SafeArrayGetLBound(array, 1, ctypes.byref(low))
        oleaut32.SafeArrayGetUBound(array, 1, ctypes.byref(high))
        numbers = []
        for at in range(low.value, high.value + 1):
            number, index = ctypes.c_int(), ctypes.c_long(at)
            oleaut32.SafeArrayGetElement(array, ctypes.byref(index), ctypes.byref(number))
            numbers.append(number.value)
        return runtime_id(numbers)
    finally:
        oleaut32.SafeArrayDestroy(array)


def _control(element: _Com, parent: str | None) -> Control:
    kind = element.read(_CONTROL_TYPE, ctypes.c_int)
    actions: list[str] = []
    value: str | None = None
    invoke = element.out(
        _PATTERN_AS, (ctypes.c_int, _PATTERN_INVOKE), (ctypes.c_void_p, ctypes.byref(_guid(_IID_INVOKE)))
    )
    if invoke:
        actions.append('press')
        invoke.release()
    pattern = element.out(
        _PATTERN_AS, (ctypes.c_int, _PATTERN_VALUE), (ctypes.c_void_p, ctypes.byref(_guid(_IID_VALUE)))
    )
    if pattern:
        try:
            value = pattern.text(_CURRENT_VALUE)
            if not pattern.read(_IS_READ_ONLY, wintypes.BOOL):
                actions.append('set_value')
        finally:
            pattern.release()
    return Control(
        role=ROLES.get(kind, f'uia:{kind}'),
        name=element.text(_NAME),
        value=value,
        enabled=bool(element.read(_IS_ENABLED, wintypes.BOOL)),
        actions=tuple(actions),
        id=_runtime_id(element),
        parent=parent,
        class_name=element.text(_CLASS_NAME),
        automation_id=element.text(_AUTOMATION_ID) or None,
        help_text=element.text(_HELP_TEXT),
        offscreen=bool(element.read(_IS_OFFSCREEN, wintypes.BOOL)),
    )


def _with_pattern(element: _Com, pattern_id: int, iid: str, use: Callable[[_Com], None]) -> None:
    pattern = element.out(_PATTERN_AS, (ctypes.c_int, pattern_id), (ctypes.c_void_p, ctypes.byref(_guid(iid))))
    try:
        use(pattern)
    finally:
        pattern.release()
