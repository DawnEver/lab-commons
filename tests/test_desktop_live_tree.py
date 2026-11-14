"""``lab_commons.desktop`` on the real Windows desktop: a nested tree is walked and acted on by id, and captured whole.

The child is a native Win32 window whose Edit and Button sit two panes deep. Pressing the button writes
the Edit's text to a file and REBUILDS the button, so the id it was pressed by is stale afterwards: the
planted control presses that id again
and must be refused (``desktop.control_gone``) without a second write. The capture test is the planted
control of the DPI defect: a window drawn in one colour, made DPI-UNAWARE on a scaled display, must have
that colour in its BOTTOM-RIGHT quadrant -- a "not black overall" check passed on the defect, whose
render filled only the top-left. Produced by a Windows host (the ``windows`` part).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import uuid
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from lab_commons import desktop
from lab_commons.proc import kill_process_tree

pytestmark = pytest.mark.windows

#: argv: title, the file a press writes. A native Win32 tree: top -> outer pane -> inner pane -> {Edit, Button 'Go'}.
#: A click writes the Edit's text and DESTROYS the button, then builds a new one (a new HWND, so a new RuntimeId).
_TREE = """
import ctypes, sys
from ctypes import wintypes as w
u, k = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
u.DefWindowProcW.restype, u.DefWindowProcW.argtypes = ctypes.c_ssize_t, (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
u.CreateWindowExW.restype = w.HWND
u.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, *(ctypes.c_int,) * 4, w.HWND, w.HMENU,
                              w.HINSTANCE, w.LPVOID)
u.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
u.DestroyWindow.argtypes = (w.HWND,)
u.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
CHILD, VISIBLE, TOP = 0x40000000, 0x10000000, 0x00CF0000
held = {}
def make(kind, text, parent, x, y, cx, cy, ident=0, style=0):
    return u.CreateWindowExW(0, kind, text, style | VISIBLE | (CHILD if parent else TOP), x, y, cx, cy, parent,
                             ident, None, None)
def build():
    held['go'] = make('Button', 'Go', held['inner'], 10, 40, 80, 24, 2)
def proc(hwnd, msg, wparam, lparam):
    if msg == 0x0111 and wparam & 0xFFFF == 2:  # WM_COMMAND from the Go button
        text = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(held['edit'], text, 256)
        open(sys.argv[2], 'a').write(text.value + '|')
        u.DestroyWindow(held['go'])
        u.PostMessageW(hwnd, 0x0401, 0, 0)
        return 0
    if msg == 0x0401:
        build()
        return 0
    if msg == 0x0002 and hwnd == held.get('top'):
        u.PostQuitMessage(0)
    return u.DefWindowProcW(hwnd, msg, wparam, lparam)
callback = PROC(proc)
class WNDCLASS(ctypes.Structure):
    _fields_ = [('style', w.UINT), ('proc', PROC), ('extra', ctypes.c_int), ('wextra', ctypes.c_int),
                ('instance', w.HINSTANCE), ('icon', w.HICON), ('cursor', w.HANDLE), ('brush', w.HBRUSH),
                ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]
u.RegisterClassW(ctypes.byref(WNDCLASS(0, callback, 0, 0, None, None, None, ctypes.c_void_p(16), None, 'LabTree')))
held['top'] = make('LabTree', sys.argv[1], None, 100, 100, 360, 220)
held['outer'] = make('LabTree', '', held['top'], 0, 0, 340, 180)
held['inner'] = make('LabTree', '', held['outer'], 0, 0, 320, 160)
held['edit'] = make('Edit', '', held['inner'], 10, 10, 200, 24, 1, 0x00800000)
build()
msg = w.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg))
    u.DispatchMessageW(ctypes.byref(msg))
"""

#: argv: title, awareness context (-1 unaware, -4 per-monitor v2). One colour fills the whole client area.
_FILL = """
import ctypes, sys, tkinter
ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(int(sys.argv[2])))
root = tkinter.Tk()
root.title(sys.argv[1])
root.configure(background='#ff0000')
root.geometry('480x360')
root.mainloop()
"""


def _until(predicate: Callable[[], bool], seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _start(script: str, *args: str) -> tuple[subprocess.Popen, desktop.Window]:
    title = f'lab-commons tree {uuid.uuid4().hex[:8]}'
    proc = subprocess.Popen([sys.executable, '-c', script, title, *args])
    seen: list[desktop.Window] = []
    if not _until(lambda: bool(seen.extend(desktop.windows(title=title)) or seen)):
        kill_process_tree(proc.pid)
        pytest.fail(f'the window {title!r} never appeared')
    return proc, seen[0]


@pytest.fixture
def tree(tmp_path: Path) -> Iterator[tuple[desktop.Window, Path]]:
    pressed = tmp_path / 'pressed.txt'
    proc, window = _start(_TREE, str(pressed))
    try:
        yield window, pressed
    finally:
        kill_process_tree(proc.pid)
        proc.wait(10)


def _go(window: desktop.Window) -> desktop.Control:
    found = [c for c in desktop.controls(window) if c.class_name == 'Button' and 'press' in c.actions]
    assert len(found) == 1, desktop.controls(window)
    return found[0]


def test_the_tree_is_walked_by_ids_and_parents(tree: tuple[desktop.Window, Path]) -> None:
    window, _ = tree
    found = desktop.controls(window)
    by_id = {c.id: c for c in found}
    assert len(by_id) == len(found), 'every control has its own id'
    assert all(c.parent is None or c.parent in by_id for c in found), 'a parent is a listed control or the window'
    depth, at = 0, _go(window)
    while at.parent is not None:
        at, depth = by_id[at.parent], depth + 1
    assert depth >= 2, 'the button sits two panes deep'
    edit = next(c for c in found if c.class_name == 'Edit')
    assert edit.automation_id == '1', 'a Win32 control id is its UIA AutomationId'
    assert edit.parent == _go(window).parent
    assert window.class_name == 'LabTree'
    assert window.owner == 0


def test_set_value_and_press_by_id_then_the_stale_id_is_gone(tree: tuple[desktop.Window, Path]) -> None:
    window, pressed = tree
    edit = next(c for c in desktop.controls(window) if c.class_name == 'Edit')
    desktop.set_value(window, text='by id', id=edit.id, owned={window.pid})
    first = _go(window).id
    desktop.press(window, id=first, owned={window.pid})
    assert _until(pressed.exists), 'the press by id never reached the button'
    assert _until(lambda: [c.id for c in desktop.controls(window) if c.class_name == 'Button'] not in ([], [first]))
    with pytest.raises(desktop.ControlGoneError) as gone:
        desktop.press(window, id=first, owned={window.pid})
    assert gone.value.reason == 'desktop.control_gone'
    time.sleep(0.5)
    assert pressed.read_text() == 'by id|', 'set by id, pressed once; the stale id pressed nothing'


def _rgb(png: bytes) -> tuple[int, int, bytes]:
    width, height = int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')
    at, data = 8, b''
    while at < len(png):
        length = int.from_bytes(png[at : at + 4], 'big')
        if png[at + 4 : at + 8] == b'IDAT':
            data += png[at + 8 : at + 8 + length]
        at += 12 + length
    raw = zlib.decompress(data)
    stride = 1 + width * 3
    return width, height, b''.join(raw[y * stride + 1 : (y + 1) * stride] for y in range(height))


@pytest.mark.parametrize('awareness', [-1, -4], ids=['dpi-unaware', 'per-monitor-v2'])
def test_planted_the_capture_fills_the_bottom_right_quadrant(awareness: int) -> None:
    proc, window = _start(_FILL, str(awareness))
    try:
        time.sleep(0.5)  # the first paint
        png = desktop.capture(window)
        width, height, rgb = _rgb(png)
        left, top, right, bottom = desktop.windows(pids={window.pid})[0].rect
        assert (width, height) == (right - left, bottom - top), 'the PNG is the physical rect'
        x0, x1, y0, y1 = width * 5 // 8, width * 7 // 8, height * 5 // 8, height * 7 // 8
        red = sum(
            1
            for y in range(y0, y1)
            for x in range(x0, x1)
            if rgb[(y * width + x) * 3] > 200 and rgb[(y * width + x) * 3 + 1] < 60
        )
        scale = ctypes.windll.user32.GetDpiForWindow(window.handle) / 96
        assert red > 0.9 * (x1 - x0) * (y1 - y0), f'bottom-right quadrant not drawn at {scale:g}x scale'
    finally:
        kill_process_tree(proc.pid)
        proc.wait(10)
