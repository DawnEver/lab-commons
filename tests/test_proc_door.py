"""THE ONE PROCESS-LAUNCH DOOR: every child the kit starts opens no console window on Windows.

INCIDENT 2026-10-16: the serve Task runs ``pythonw`` (no console) every five minutes, and a console child
a console-less parent starts allocates a console of its OWN -- a window on the user's desktop. The host
refresh's git and uv calls were started that way. :func:`lab_commons.proc.run` and
:func:`lab_commons.proc.popen` are the only spawners; they always add ``CREATE_NO_WINDOW`` on Windows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lab_commons import proc

_ON_WINDOWS = sys.platform == 'win32'


def _seen_flags(monkeypatch: pytest.MonkeyPatch, call: str, **kwargs: object) -> object:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, 'run' if call == 'run' else 'Popen', lambda *_a, **kw: seen.update(kw))
    getattr(proc, call)(['x'], **kwargs)
    return seen.get('creationflags')


@pytest.mark.parametrize('call', ['run', 'popen'])
def test_the_door_adds_no_window_and_keeps_the_callers_flags(monkeypatch: pytest.MonkeyPatch, call: str) -> None:
    expected = subprocess.CREATE_NO_WINDOW if _ON_WINDOWS else None
    assert _seen_flags(monkeypatch, call) == expected
    if _ON_WINDOWS:
        below = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        assert _seen_flags(monkeypatch, call, creationflags=below) == below | subprocess.CREATE_NO_WINDOW


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
from lab_commons import proc
done = proc.run([sys.argv[1], sys.argv[2]], capture_output=True, text=True, encoding='utf-8',
                stdin=subprocess.DEVNULL, timeout=60, check=True)
open(sys.argv[3], 'w', encoding='utf-8').write(done.stdout)
"""


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
    proc.run([str(pythonw), str(parent), sys.executable, str(reporter), str(out)], check=True, timeout=120)
    assert json.loads(out.read_text(encoding='utf-8')) == {'child': False, 'grandchild': False}
