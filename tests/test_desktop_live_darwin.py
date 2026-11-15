"""``lab_commons.desktop`` on a real macOS session: a native dialog is listed, captured, read over AX, pressed.

The dialog is ``osascript``'s ``display dialog``: an AppKit window owned by the ``osascript`` process, so it is
on the Accessibility tree (Tk 8.6's Aqua widgets are not). Its answer is the process's exit code and stdout, so
a press is observed by its effect. macOS gates titles and captures behind Screen Recording, and controls and acts
behind Accessibility, which a host may not grant: each such refusal is an XFAIL carrying its reason code
``desktop.no_accessibility`` and the refusal's own text -- never a silent skip, never a pass. Produced by a macOS
host (the ``macos`` part).
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from lab_commons import desktop

pytestmark = pytest.mark.macos

_DIALOG = """on run argv
  display dialog "Proceed with the run?" with title (item 1 of argv) buttons {"Cancel", "OK"} default button "OK"
end run"""


def _until(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@contextmanager
def _permitted() -> Iterator[None]:
    """A ``desktop.no_accessibility`` refusal is an xfail naming that reason; any other error stays an error."""
    try:
        yield
    except desktop.UnsupportedError as refused:
        if refused.reason != 'desktop.no_accessibility':
            raise
        pytest.xfail(f'{refused.reason}: {refused}')


@dataclass(frozen=True)
class App:
    proc: subprocess.Popen
    dialog: desktop.Window


@pytest.fixture
def app() -> Iterator[App]:
    title = f'lab-commons desktop {uuid.uuid4().hex[:8]}'
    proc = subprocess.Popen(
        ['/usr/bin/osascript', '-e', _DIALOG, title], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        owned: list[desktop.Window] = []

        def shown() -> bool:
            owned[:] = desktop.windows(pids={proc.pid})
            return any(w.title == title for w in owned)

        with _permitted():
            appeared = _until(shown, 20.0)
        if not appeared and owned and all(not w.title for w in owned):
            pytest.xfail(
                f'desktop.no_accessibility: pid {proc.pid} owns windows whose titles read empty '
                '(window titles need Screen Recording)'
            )
        if not appeared:
            said = proc.communicate(timeout=5)[1] if proc.poll() is not None else '(still running)'
            every = desktop.windows()
            others = (len(every), sorted({(w.pid, w.title) for w in every if w.visible}))
            pytest.fail(
                f'the dialog {title!r} never appeared; pid {proc.pid} owns {owned}; osascript exit '
                f'{proc.returncode}: {said.strip()!r}; windows (all, visible (pid, title)): {others}'
            )
        yield App(proc, next(w for w in owned if w.title == title))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)


def _answer(app: App) -> str | None:
    """What the dialog answered: ``OK`` or ``Cancel``, or ``None`` while it is still up."""
    if not _until(lambda: app.proc.poll() is not None, 5.0):
        return None
    out = app.proc.stdout.read() if app.proc.stdout else ''
    return 'OK' if 'button returned:OK' in out else 'Cancel'


def test_the_dialog_is_listed_with_its_own_pid(app: App) -> None:
    assert app.dialog.pid == app.proc.pid
    assert app.dialog.visible


def test_the_dialog_is_captured_as_a_png(app: App) -> None:
    with _permitted():
        png = desktop.capture(app.dialog)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')
    assert width > 0
    assert height > 0  # a Retina capture is the window's points times the backing scale, so no exact size


def test_the_dialog_is_read_without_ocr(app: App) -> None:
    with _permitted():
        found = desktop.controls(app.dialog)
    assert any('Proceed with the run?' in {c.name, c.value} for c in found), found
    buttons = sorted((c.name, c.role, 'press' in c.actions) for c in found if c.name in {'OK', 'Cancel'})
    assert buttons == [('Cancel', 'button', True), ('OK', 'button', True)], found


def test_ok_is_pressed_once_and_the_answer_is_observed(app: App) -> None:
    with _permitted():
        desktop.press(app.dialog, 'OK', owned={app.dialog.pid})
    assert _answer(app) == 'OK'


def test_planted_a_pid_we_do_not_own_leaves_the_dialog_untouched(app: App) -> None:
    with pytest.raises(desktop.NotOwnedError):
        desktop.press(app.dialog, 'OK', owned={app.dialog.pid + 1})
    time.sleep(0.5)
    assert app.proc.poll() is None


def test_a_label_is_exact(app: App) -> None:
    with _permitted(), pytest.raises(desktop.LabelNotFoundError):
        desktop.press(app.dialog, 'ok', owned={app.dialog.pid})
    assert app.proc.poll() is None
