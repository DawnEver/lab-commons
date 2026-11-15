"""``lab_commons.desktop`` on a real X11 session: a GTK dialog is listed, captured, read over AT-SPI, pressed.

The session is the test's own: ``Xvfb`` on a free display, a window manager (``_NET_CLIENT_LIST`` is the window
manager's list, so a bare X server lists nothing), a private session D-Bus that activates the AT-SPI bus and its
registry, and ``zenity --question`` as the dialog -- GTK, so it is on AT-SPI (tkinter is not). The answer is the
dialog's exit code, so a press is observed by its effect. A missing tool FAILS naming the tool and its package,
never skips. Produced by a Linux host (the ``linux`` part).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final

import pytest

from lab_commons import desktop

pytestmark = pytest.mark.linux

#: The tools the session needs, each with the Debian/Ubuntu package that ships it.
TOOLS: Final = {
    'Xvfb': 'xvfb',
    'dbus-daemon': 'dbus',
    'zenity': 'zenity',
    'xprop': 'x11-utils',
    'xwininfo': 'x11-utils',
    'xwd': 'x11-apps',
    'gdbus': 'libglib2.0-bin',
    'openbox': 'openbox',
}
#: ``at-spi2-core`` ships no command the session calls: the session bus activates ``org.a11y.Bus`` from it.
PACKAGES: Final = sorted({*TOOLS.values(), 'at-spi2-core'})


def _until(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _path(tool: str) -> str:
    found = shutil.which(tool)
    assert found, f'{tool} is not on PATH (apt-get install {TOOLS[tool]})'
    return found


def _first_line(proc: subprocess.Popen, what: str) -> str:
    line = proc.stdout.readline().strip() if proc.stdout else ''
    assert line, f'{what} printed nothing (exit {proc.poll()})'
    return line


@pytest.fixture(scope='module')
def session() -> Iterator[dict[str, str]]:
    """``DISPLAY`` and ``DBUS_SESSION_BUS_ADDRESS`` of a fresh X11 + D-Bus + window-manager session, set in env."""
    missing = sorted(tool for tool in TOOLS if shutil.which(tool) is None)
    if missing:
        pytest.fail(f'the X11 session needs {missing}: apt-get install {" ".join(PACKAGES)}', pytrace=False)
    started: list[subprocess.Popen] = []
    saved = {name: os.environ.get(name) for name in ('DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NO_AT_BRIDGE')}
    try:
        xvfb = subprocess.Popen(
            [_path('Xvfb'), '-displayfd', '1', '-screen', '0', '1280x1024x24', '-nolisten', 'tcp'],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        started.append(xvfb)
        env = {'DISPLAY': f':{_first_line(xvfb, "Xvfb -displayfd")}'}
        bus = subprocess.Popen(
            [_path('dbus-daemon'), '--session', '--nofork', '--nopidfile', '--print-address=1'],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        started.append(bus)
        env['DBUS_SESSION_BUS_ADDRESS'] = _first_line(bus, 'dbus-daemon --print-address')
        os.environ.update(env)
        os.environ.pop('NO_AT_BRIDGE', None)
        started.append(subprocess.Popen([_path('openbox'), '--sm-disable'], stdout=subprocess.DEVNULL))

        def managed() -> bool:
            done = subprocess.run(
                [_path('xprop'), '-root', '_NET_SUPPORTING_WM_CHECK'], capture_output=True, check=False
            )
            return b'window id' in done.stdout

        assert _until(managed), 'openbox never managed the display (_NET_SUPPORTING_WM_CHECK unset)'
        yield env
    finally:
        for proc in reversed(started):
            proc.terminate()
            proc.wait(10)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@dataclass(frozen=True)
class App:
    proc: subprocess.Popen
    dialog: desktop.Window


@pytest.fixture
def app(session: dict[str, str]) -> Iterator[App]:  # noqa: ARG001 -- the session it runs in
    title = f'lab-commons desktop {uuid.uuid4().hex[:8]}'
    argv = [_path('zenity'), '--question', '--title', title, '--text', 'Proceed with the run?', '--ok-label', 'OK']
    proc = subprocess.Popen([*argv, '--cancel-label', 'Cancel'], env=os.environ | {'GDK_BACKEND': 'x11'})
    try:
        seen: list[desktop.Window] = []

        def shown() -> bool:
            seen[:] = desktop.windows(title=title)
            return len(seen) == 1

        assert _until(shown, 20.0), f'the dialog {title!r} never appeared once: {seen}'
        yield App(proc, seen[0])
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)


def _answer(app: App) -> int | None:
    """The dialog's exit code once it has answered (0 OK, 1 Cancel), else ``None``."""
    return app.proc.returncode if _until(lambda: app.proc.poll() is not None, 5.0) else None


def test_the_dialog_is_listed_with_its_own_pid(app: App) -> None:
    assert app.dialog.pid == app.proc.pid
    assert app.dialog.visible
    assert app.dialog.enabled is None, 'X11 says nothing about modal-blocked input'


def test_the_dialog_is_captured_as_a_png_of_its_size(app: App) -> None:
    png = desktop.capture(app.dialog)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')
    left, top, right, bottom = app.dialog.rect
    assert (width, height) == (right - left, bottom - top)


def test_the_dialog_is_read_without_ocr(app: App) -> None:
    found = desktop.controls(app.dialog)
    tree = '; '.join(f'{c.id[-8:]}<{(c.parent or "")[-8:]} {c.role}:{c.name!r}:{",".join(c.actions)}' for c in found)
    assert any(c.name == 'Proceed with the run?' or c.value == 'Proceed with the run?' for c in found), tree
    buttons = sorted((c.name, c.role, 'press' in c.actions) for c in found if c.name in {'OK', 'Cancel'})
    assert buttons == [('Cancel', 'button', True), ('OK', 'button', True)], tree


def test_ok_is_pressed_once_and_the_answer_is_observed(app: App) -> None:
    desktop.press(app.dialog, 'OK', owned={app.dialog.pid})
    assert _answer(app) == 0


def test_planted_a_pid_we_do_not_own_leaves_the_dialog_untouched(app: App) -> None:
    with pytest.raises(desktop.NotOwnedError):
        desktop.press(app.dialog, 'OK', owned={app.dialog.pid + 1})
    time.sleep(0.5)
    assert app.proc.poll() is None


def test_a_label_is_exact(app: App) -> None:
    with pytest.raises(desktop.LabelNotFoundError):
        desktop.press(app.dialog, 'ok', owned={app.dialog.pid})
    assert app.proc.poll() is None


def test_a_frame_with_no_atspi_close_action_is_refused_by_name(app: App) -> None:
    """A GTK frame exposes no AT-SPI ``close`` action: close refuses by name and the dialog stays up."""
    with pytest.raises(desktop.UnsupportedError) as refused:
        desktop.close(app.dialog, owned={app.dialog.pid})
    assert refused.value.reason == 'desktop.not_closable'
    assert app.proc.poll() is None
