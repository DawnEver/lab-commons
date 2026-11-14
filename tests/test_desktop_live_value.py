"""``lab_commons.desktop.set_value`` on the real Windows desktop, against a UIA ValuePattern the test owns.

The child is a plain Win32 window built through ctypes: a STATIC label ``Run name:`` followed by an EDIT
control, so the system's UIA proxy names the edit after its label (the shape of every vendor dialog field).
The value is read back twice -- through ``controls`` (UIA) and through the child itself, which mirrors the
edit's own text to a file -- so a set that only fooled UIA would not pass. Produced by a Windows host (the
``windows`` part).
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from lab_commons import desktop
from lab_commons.proc import kill_process_tree

pytestmark = pytest.mark.windows

_APP = r"""
import ctypes, sys
from ctypes import wintypes as wt
user32, kernel32 = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = (wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.SetTimer.argtypes = (wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
kernel32.GetModuleHandleW.restype = wt.HMODULE
edit = None
def proc(hwnd, msg, wparam, lparam):
    if msg == 0x0113:  # WM_TIMER: mirror the edit's own text to the file
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(edit, buffer, 256)
        open(sys.argv[2], 'w', encoding='utf-8').write(buffer.value)
        return 0
    if msg == 0x0002:  # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
callback = WNDPROC(proc)
class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wt.UINT), ('proc', WNDPROC), ('extra', ctypes.c_int), ('wextra', ctypes.c_int),
                ('instance', wt.HINSTANCE), ('icon', wt.HICON), ('cursor', wt.HANDLE), ('brush', wt.HBRUSH),
                ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]
instance = kernel32.GetModuleHandleW(None)
klass = WNDCLASSW(0, callback, 0, 0, instance, None, None, wt.HBRUSH(16), None, 'LabCommonsValue')
user32.RegisterClassW(ctypes.byref(klass))
main = user32.CreateWindowExW(0, 'LabCommonsValue', sys.argv[1], 0x10CF0000, 100, 100, 360, 140,
                              None, None, instance, None)  # WS_OVERLAPPEDWINDOW | WS_VISIBLE
user32.CreateWindowExW(0, 'STATIC', 'Run name:', 0x50000000, 10, 20, 90, 20, main, None, instance, None)
edit = user32.CreateWindowExW(0x200, 'EDIT', 'first', 0x50010080, 110, 18, 200, 24, main, None, instance, None)
user32.SetTimer(main, 1, 100, None)
msg = wt.MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))
"""


def _until(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def form(tmp_path: Path) -> Iterator[tuple[desktop.Window, Path]]:
    title = f'lab-commons value {uuid.uuid4().hex[:8]}'
    mirror = tmp_path / 'edit.txt'
    proc = subprocess.Popen([sys.executable, '-c', _APP, title, str(mirror)])
    try:
        found: list[desktop.Window] = []

        def listed() -> bool:
            found[:] = desktop.windows(title=title)
            return bool(found)

        assert _until(listed, 20.0), f'the window {title!r} never appeared'
        yield found[0], mirror
    finally:
        kill_process_tree(proc.pid)  # the launcher AND the child that owns the window
        proc.wait(10)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def test_the_labelled_edit_is_listed_with_its_value_and_settable(form: tuple[desktop.Window, Path]) -> None:
    window, _ = form
    found = desktop.controls(window)
    edits = [c for c in found if c.name == 'Run name:' and c.role == 'edit']
    assert len(edits) == 1, found
    assert edits[0].value == 'first'
    assert 'set_value' in edits[0].actions


def test_set_value_reads_back_through_uia_and_the_control_itself(form: tuple[desktop.Window, Path]) -> None:
    window, mirror = form
    desktop.set_value(window, 'Run name:', 'second run', owned={window.pid})
    assert [c.value for c in desktop.controls(window) if c.role == 'edit'] == ['second run']
    assert _until(lambda: _read(mirror) == 'second run'), _read(mirror)


def test_planted_a_pid_we_do_not_own_leaves_the_value_untouched(form: tuple[desktop.Window, Path]) -> None:
    window, mirror = form
    with pytest.raises(desktop.NotOwnedError):
        desktop.set_value(window, 'Run name:', 'stolen', owned={window.pid + 1})
    time.sleep(0.5)
    assert _until(lambda: _read(mirror) == 'first'), _read(mirror)
