"""THE ONE PROCESS-LAUNCH DOOR: every child the kit starts opens no console window on Windows.

INCIDENT 2026-10-16: the serve Task runs ``pythonw`` (no console) every five minutes, and a console child
a console-less parent starts allocates a console of its OWN -- a window on the user's desktop. The host
refresh's git and uv calls were started that way. :func:`lab_commons.launch.run` and
:func:`lab_commons.launch.popen` are the only spawners; a console-less parent adds ``CREATE_NO_WINDOW``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lab_commons import launch

_ON_WINDOWS = sys.platform == 'win32'


#: The real ``CREATE_NO_WINDOW`` and ``BELOW_NORMAL_PRIORITY_CLASS`` values, readable on every platform.
_NO_WINDOW, _BELOW = 0x08000000, 0x00004000


def _seen_flags(monkeypatch: pytest.MonkeyPatch, call: str, *, console: bool, **kwargs: object) -> object:
    seen: dict[str, object] = {}

    def spy(*_args: object, **kw: object) -> None:
        seen.update(kw)

    monkeypatch.setattr(launch, 'console_attached', lambda: console)
    if call == 'run':
        monkeypatch.setattr(subprocess, 'run', spy)
        launch.run(['x'], **kwargs)
    else:
        monkeypatch.setattr(subprocess, 'Popen', spy)
        launch.popen(['x'], **kwargs)
    return seen.get('creationflags')


@pytest.mark.parametrize('call', ['run', 'popen'])
def test_a_console_less_parent_adds_no_window_keeping_the_callers_flags(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    """Console-less (the serve's ``pythonw``): the flag, OR'd into the caller's; under a console: untouched."""
    no_window, below = _NO_WINDOW, _BELOW
    assert _seen_flags(monkeypatch, call, console=False) == no_window
    assert _seen_flags(monkeypatch, call, console=False, creationflags=below) == below | no_window
    assert _seen_flags(monkeypatch, call, console=True) is None
    assert _seen_flags(monkeypatch, call, console=True, creationflags=below) == below


def test_this_process_reads_its_console() -> None:
    assert launch.console_attached() in {True, False}


#: A console child reporting whether it has a console WINDOW, then starting a PLAIN grandchild that reports the same.
_REPORTER = """
import ctypes, json, subprocess, sys
mine = bool(ctypes.windll.kernel32.GetConsoleWindow())
probe = 'import ctypes; print(bool(ctypes.windll.kernel32.GetConsoleWindow()))'
grand = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, encoding='utf-8',
                       check=True, timeout=60)
print(json.dumps({'child': mine, 'grandchild': grand.stdout.strip() == 'True'}))
"""

#: The windowless parent: ``pythonw`` has no console, and starts the reporter through the door.
_PARENT = """
import subprocess, sys
from lab_commons import launch
done = launch.run([sys.argv[1], sys.argv[2]], capture_output=True, text=True, encoding='utf-8',
                stdin=subprocess.DEVNULL, timeout=60, check=True)
open(sys.argv[3], 'w', encoding='utf-8').write(done.stdout)
"""


@pytest.mark.not_applicable
@pytest.mark.skipif(not _ON_WINDOWS, reason='a console window exists only on Windows')
def test_a_windowless_parents_child_and_its_plain_grandchild_have_no_window(tmp_path: Path) -> None:
    """MEASURED: a windowless parent's child and that child's plain grandchild both have no window.

    CREATE_NO_WINDOW gives the child a console with no window, and a grandchild started WITHOUT
    the flag (a pytest-xdist worker, git under uv) inherits that console -- so it has none either.
    """
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    reporter, parent, out = tmp_path / 'reporter.py', tmp_path / 'parent.py', tmp_path / 'out.json'
    reporter.write_text(_REPORTER, encoding='utf-8')
    parent.write_text(_PARENT, encoding='utf-8')
    launch.run([str(pythonw), str(parent), sys.executable, str(reporter), str(out)], check=True, timeout=120)
    assert json.loads(out.read_text(encoding='utf-8')) == {'child': False, 'grandchild': False}
