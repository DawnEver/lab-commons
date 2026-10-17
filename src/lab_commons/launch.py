"""Tier 1 -- THE ONE PROCESS-LAUNCH DOOR: every child the kit starts opens no console window on Windows.

INCIDENT 2026-10-16: the serve Task runs ``pythonw`` (no console) every five minutes, and every console
child it started directly -- git and uv in the host refresh -- allocated a console of its OWN: a window on
the user's desktop. :func:`run` and :func:`popen` are the kit's only spawners
(``tests/test_arch_one_door_launches_every_process.py`` refuses any other in ``src``).

THE FLAG, AND ONLY WHEN THIS PROCESS HAS NO CONSOLE (:func:`console_attached`). ``CREATE_NO_WINDOW``
gives the child a console WITHOUT a window, and every descendant started plainly -- a pytest-xdist worker,
git under uv -- inherits it (MEASURED in ``tests/test_launch.py``: child and grandchild report no window).
Under a console -- visible, or the hidden one a windowless child already holds -- the flag would hand the
child a NEW hidden console and cut it off from the inherited console streams (MEASURED: a pytest run under
``verify``'s job object printed nothing), while a plain child shares the console and opens no window.
The caller's own flags are kept. Stdlib only; resolved at call time, so a test patching
``subprocess.run`` still sees it.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any, Final

__all__ = ['console_attached', 'popen', 'run']

#: ``subprocess.CREATE_NO_WINDOW``, spelled so the module imports where that name does not exist.
CREATE_NO_WINDOW: Final = 0x08000000


def console_attached() -> bool:
    """Whether this process has a console (a visible OR a hidden one); always ``True`` off Windows."""
    return sys.platform != 'win32' or bool(ctypes.windll.kernel32.GetConsoleCP())


def _windowless(kwargs: dict[str, Any]) -> dict[str, Any]:
    if not console_attached():
        kwargs['creationflags'] = int(kwargs.get('creationflags') or 0) | CREATE_NO_WINDOW
    return kwargs


def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:  # noqa: ANN401 -- subprocess.run's own signature
    """:func:`subprocess.run` through the door: a waited child that opens no window."""
    return subprocess.run(*args, **_windowless(kwargs))  # noqa: PLW1510 -- the caller decides check


def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:  # noqa: ANN401 -- Popen's own signature
    """:class:`subprocess.Popen` through the door: a managed child that opens no window."""
    return subprocess.Popen(*args, **_windowless(kwargs))
