"""The macOS backend: CGWindowList and System Events through ``osascript`` (JXA), ``screencapture -l``.

Every value (pid, title, label, text) reaches a script as an ``argv`` item of ``osascript``, never spliced into its
source, so a title or a typed value cannot inject. macOS gates both halves behind privacy permissions the agent
cannot grant itself: window titles and captures need Screen Recording, controls and acts need Accessibility, for
the terminal that runs the caller. A refusal from either is raised by name (``desktop.no_accessibility``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from lab_commons.desktop._core import Control, UnsupportedError, Window, WindowBusyError, pick
from lab_commons.temp import TEMP_PREFIX

__all__ = ['CLOSE', 'CONTROLS', 'LIST', 'PRESS', 'SET_VALUE', 'Darwin']

#: ``argv, timeout -> stdout bytes``; raises ``subprocess.CalledProcessError`` / ``TimeoutExpired``.
Runner = Callable[[list[str], float], bytes]

#: Every window of every layer; ``layer == 0`` (an application window) and on-screen is what reads visible.
LIST = """ObjC.import('CoreGraphics');
function run(argv) {
  const all = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(0, 0)));
  return JSON.stringify(all.map(w => ({id: w.kCGWindowNumber, pid: w.kCGWindowOwnerPID,
    title: w.kCGWindowName || '', layer: w.kCGWindowLayer, onscreen: !!w.kCGWindowIsOnscreen,
    x: w.kCGWindowBounds.X, y: w.kCGWindowBounds.Y, w: w.kCGWindowBounds.Width, h: w.kCGWindowBounds.Height})));
}"""
# argv: pid, title. The window of process <pid> named exactly <title>, its UI tree flattened in a stable order.
_FIND = """const se = Application('System Events');
function win(argv) { return se.processes.whose({unixId: Number(argv[0])})[0].windows.whose({name: argv[1]})[0]; }
function get(e, f) {
  try { const v = e[f](); return v === null || v === undefined ? null : v; } catch (_) { return null; }
}
function names(e) { try { return e.actions().map(a => a.name()); } catch (_) { return []; } }
"""
CONTROLS = (
    _FIND
    + """function walk(e, path, parent, out) {
  let kids = [];
  try { kids = e.uiElements(); } catch (_) {}
  kids.forEach((k, i) => {
    const id = path === '' ? String(i) : path + '.' + i;
    let ident = null;
    try { ident = k.attributes.byName('AXIdentifier').value(); } catch (_) {}
    out.push({id: id, parent: parent, role: get(k, 'role') || '', name: get(k, 'name') || '', value: get(k, 'value'),
      enabled: get(k, 'enabled') !== false, actions: names(k), help: get(k, 'help') || '', identifier: ident});
    walk(k, id, id, out);
  });
  return out;
}
function run(argv) { return JSON.stringify(walk(win(argv), '', null, [])); }"""
)
# argv: pid, title, index path, name[, text]. The element is re-read at its path and must still carry <name>.
_ELEMENT = """function element(argv) {
  let e = win(argv);
  argv[2].split('.').forEach(i => { e = e.uiElements()[Number(i)]; });
  if (String(get(e, 'name') || '') !== argv[3]) throw new Error('desktop: the control named ' + argv[3] + ' moved');
  return e;
}
"""
PRESS = _FIND + _ELEMENT + """function run(argv) { element(argv).actions.byName('AXPress').perform(); }"""
SET_VALUE = _FIND + _ELEMENT + """function run(argv) { element(argv).value = argv[4]; }"""
CLOSE = (
    _FIND
    + """function run(argv) {
  win(argv).buttons.whose({subrole: 'AXCloseButton'})[0].actions.byName('AXPress').perform();
}"""
)

#: The AX roles whose value System Events may set.
_EDITABLE = frozenset({'AXTextField', 'AXTextArea', 'AXComboBox'})


def _run(argv: list[str], timeout_s: float) -> bytes:
    return subprocess.run(argv, capture_output=True, check=True, timeout=timeout_s).stdout


def window_from(row: dict) -> Window:
    """A :class:`Window` from one :data:`LIST` row."""
    left, top = int(row['x']), int(row['y'])
    return Window(
        handle=int(row['id']),
        pid=int(row['pid']),
        title=str(row['title']),
        rect=(left, top, left + int(row['w']), top + int(row['h'])),
        visible=bool(row['onscreen']) and int(row['layer']) == 0,
        enabled=None,
        responding=None,
    )


def control_from(row: dict) -> Control:
    """A :class:`Control` from one :data:`CONTROLS` row (``AXButton`` reads ``button``)."""
    role = str(row['role'])
    actions = ('press',) if 'AXPress' in row['actions'] else ()
    if role in _EDITABLE:
        actions = (*actions, 'set_value')
    value = row['value']
    return Control(
        role=role.removeprefix('AX').lower() or 'unknown',
        name=str(row['name']),
        value=None if value is None else str(value),
        enabled=bool(row['enabled']),
        actions=actions,
        id=str(row['id']),
        parent=None if row['parent'] is None else str(row['parent']),
        automation_id=None if row['identifier'] in (None, '') else str(row['identifier']),
        help_text=str(row['help']),
    )


class Darwin:
    """A macOS GUI session; *run* and *which* are the seams the unit tests fill."""

    def __init__(self, *, run: Runner = _run, which: Callable[[str], str | None] = shutil.which) -> None:
        self._run = run
        self._which = which

    def _jxa(self, script: str, *argv: object, timeout_s: float = 15.0) -> str:
        if self._which('osascript') is None:
            msg = 'osascript is absent: this is not a macOS GUI session'
            raise UnsupportedError(msg, 'desktop.no_accessibility')
        try:
            out = self._run(['osascript', '-l', 'JavaScript', '-e', script, *map(str, argv)], timeout_s)
        except subprocess.TimeoutExpired as late:
            msg = f'System Events did not answer within {timeout_s:g} s'
            raise WindowBusyError(msg) from late
        except subprocess.CalledProcessError as refused:
            said = (refused.stderr or b'').decode(errors='replace').strip()
            if 'moved' in said:
                raise
            msg = (
                f'osascript refused ({said}): grant the terminal Accessibility (controls, acts) and Screen Recording '
                '(titles, captures) in System Settings > Privacy & Security'
            )
            raise UnsupportedError(msg, 'desktop.no_accessibility') from refused
        return out.decode(errors='replace').strip()

    def windows(self) -> list[Window]:
        return [window_from(row) for row in json.loads(self._jxa(LIST) or '[]')]

    def capture(self, window: Window, timeout_s: float) -> bytes:
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            path = Path(tmp) / 'window.png'
            try:
                self._run(['screencapture', '-x', '-o', '-l', str(window.handle), '-t', 'png', str(path)], timeout_s)
            except subprocess.TimeoutExpired as late:
                msg = f'screencapture did not capture window {window.handle} within {timeout_s:g} s'
                raise WindowBusyError(msg) from late
            if not path.is_file():
                msg = 'screencapture wrote nothing: grant the terminal Screen Recording in System Settings > Privacy'
                raise UnsupportedError(msg, 'desktop.no_accessibility')
            return path.read_bytes()

    def controls(self, window: Window) -> list[Control]:
        return [control_from(row) for row in json.loads(self._jxa(CONTROLS, window.pid, window.title) or '[]')]

    def _target(self, window: Window, act: str, label: str | None, id: str | None) -> Control:  # noqa: A002
        return pick(window, act, [(c, c) for c in self.controls(window)], label=label, id=id)

    def press(self, window: Window, label: str | None, id: str | None) -> None:  # noqa: A002 -- Control.id
        target = self._target(window, 'press', label, id)
        self._jxa(PRESS, window.pid, window.title, target.id, target.name)

    def set_value(self, window: Window, label: str | None, id: str | None, text: str) -> None:  # noqa: A002
        target = self._target(window, 'set_value', label, id)
        self._jxa(SET_VALUE, window.pid, window.title, target.id, target.name, text)

    def close(self, window: Window) -> None:
        self._jxa(CLOSE, window.pid, window.title)
