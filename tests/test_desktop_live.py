"""``lab_commons.desktop`` on the real Windows desktop: a modal dialog is listed, captured, read, pressed, closed.

The child is a tkinter window that raises a NATIVE Win32 message box (``askokcancel``) -- the shape of every
recorded vendor block: a main window disabled by a modal whose labelled buttons decide. The answer is written
to a file, so a press is observed by its effect. The window's pid is read from the window (a venv
``python.exe`` is a launcher whose child owns it); every act names it, and the planted control names a pid
we do not own and must leave the dialog untouched. Produced by a Windows host (the ``windows`` part).
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from lab_commons import desktop
from lab_commons.proc import kill_process_tree

pytestmark = pytest.mark.windows

_APP = """
import sys, tkinter
from tkinter import messagebox
root = tkinter.Tk()
root.title(sys.argv[1])
root.geometry('320x120')
def ask():
    answer = messagebox.askokcancel(sys.argv[1] + ' dialog', 'Proceed with the run?', parent=root)
    open(sys.argv[2], 'w').write(str(answer))
root.after(300, ask)
root.mainloop()
"""


@dataclass(frozen=True)
class App:
    proc: subprocess.Popen
    main: desktop.Window
    dialog: desktop.Window
    answer: Path


def _until(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    title = f'lab-commons desktop {uuid.uuid4().hex[:8]}'
    answer = tmp_path / 'answer.txt'
    proc = subprocess.Popen([sys.executable, '-c', _APP, title, str(answer)])
    try:
        seen: dict[str, desktop.Window] = {}

        def both() -> bool:
            for window in desktop.windows(title=title):
                seen['dialog' if window.title.endswith(' dialog') else 'main'] = window
            return len(seen) == 2

        assert _until(both, 20.0), f'the window {title!r} and its dialog never both appeared: {seen}'
        yield App(proc, seen['main'], seen['dialog'], answer)
    finally:
        kill_process_tree(proc.pid)  # the launcher AND the child that owns the window
        proc.wait(10)


def test_the_modal_disables_its_main_window_and_both_are_one_process(app: App) -> None:
    assert app.main.pid == app.dialog.pid
    assert app.main.enabled is False, 'a modal owns its input'
    assert app.dialog.enabled is True
    assert app.dialog.responding is True
    assert app.dialog.visible


def test_the_dialog_is_captured_as_a_png_of_its_physical_size(app: App) -> None:
    png = desktop.capture(app.dialog)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')
    left, top, right, bottom = app.dialog.rect
    assert (width, height) == (right - left, bottom - top)


def test_the_dialog_is_read_without_ocr(app: App) -> None:
    found = desktop.controls(app.dialog)
    assert any(c.name == 'Proceed with the run?' for c in found), found
    assert [(c.role, 'press' in c.actions) for c in found if c.name in {'OK', 'Cancel'}] == [
        ('button', True),
        ('button', True),
    ]


def test_ok_is_pressed_once_and_the_answer_is_observed(app: App) -> None:
    desktop.press(app.dialog, 'OK', owned={app.dialog.pid})
    assert _until(app.answer.exists), 'the dialog never answered'
    assert app.answer.read_text() == 'True'


def test_planted_a_pid_we_do_not_own_leaves_the_dialog_untouched(app: App) -> None:
    with pytest.raises(desktop.NotOwnedError):
        desktop.press(app.dialog, 'OK', owned={app.dialog.pid + 1})  # never a Windows pid (multiples of 4)
    time.sleep(0.5)
    assert not app.answer.exists()


def test_a_label_is_exact(app: App) -> None:
    with pytest.raises(desktop.LabelNotFoundError):
        desktop.press(app.dialog, 'ok', owned={app.dialog.pid})
    assert not app.answer.exists()


def test_close_dismisses_the_dialog_as_its_own_close_would(app: App) -> None:
    desktop.close(app.dialog, owned={app.dialog.pid})
    assert _until(app.answer.exists), 'the dialog did not close'
    assert app.answer.read_text() == 'False'
