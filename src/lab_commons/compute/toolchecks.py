"""HOW EACH VENDOR TOOL IS READ -- find its executable, read its version, run its smoke.

The readers :mod:`lab_commons.compute.presence` turns into one verdict per tool. Every reader of the OS
is a module function, so a test passes fakes.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if sys.platform == 'win32':
    import ctypes
    import winreg

__all__ = [
    'CHECKS',
    'SIMBA_SMOKE',
    'Check',
    'femm_find',
    'file_version',
    'jmag_find',
    'matlab_find',
    'matlab_version',
    'node_find',
    'simba_find',
]

#: The longest one smoke may run (MATLAB's first start on a cold box is the slow one).
SMOKE_TIMEOUT_S: Final = 300.0

_SYSTEMS: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}


@dataclass(frozen=True)
class Check:
    """How one tool is read: *find* its executable, read its *version*, run its *smoke*.

    *find* returns ``(source, path)`` or ``None``; *version* ``(source, value)`` or ``None``; *smoke*
    ``(passed, detail)``.
    """

    find: Callable[[], tuple[str, Path] | None]
    version: Callable[[Path], tuple[str, str] | None]
    smoke: Callable[[Path], tuple[bool, str]]


# --- the readers ---------------------------------------------------------------------------------------


def _run(argv: Sequence[str], timeout: float = SMOKE_TIMEOUT_S) -> tuple[bool, str]:
    """Run *argv*; ``(exit 0, the last line of its output or why it did not run)``."""
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as failed:
        return False, f'{type(failed).__name__}: {failed}'
    lines = [line.strip() for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return done.returncode == 0, f'exit {done.returncode}: {lines[-1] if lines else "no output"}'


def _first_line(argv: Sequence[str]) -> str | None:
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return lines[0] if done.returncode == 0 and lines else None


def file_version(path: Path) -> str | None:
    """The Windows file version (``a.b.c.d``) of *path*; ``None`` off Windows or when it carries none."""
    if sys.platform != 'win32':
        return None
    api = ctypes.windll.version
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    block, length = ctypes.c_void_p(), ctypes.c_uint()
    if not api.VerQueryValueW(data, '\\', ctypes.byref(block), ctypes.byref(length)) or not length.value:
        return None
    words = ctypes.cast(block, ctypes.POINTER(ctypes.c_uint32 * 4)).contents  # VS_FIXEDFILEINFO head
    ms, ls = words[2], words[3]
    return f'{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}'


def _versioned(flag_argv: Callable[[Path], Sequence[str]]) -> Callable[[Path], tuple[str, str] | None]:
    """The version a ``--version`` run prints, else the executable's file version."""

    def read(path: Path) -> tuple[str, str] | None:
        if printed := _first_line(flag_argv(path)):
            return '--version', printed
        found = file_version(path)
        return ('file version', found) if found else None

    return read


#: The FEMM 5 headless engine per OS: its binary and the install directory used when ``FEMM_DIR`` is unset.
FEMM_HEADLESS: Final = {
    'linux': ('femm_headless', '~/.local/opt/femm-qt/bin'),
    'windows': ('femm_headlessw.exe', 'C:/femm-qt/bin'),
}


def femm_find(
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, Path] | None:
    """The FEMM 5 headless binary: in ``FEMM_DIR`` (or its ``bin``), else the standard install, else ``PATH``."""
    system = _SYSTEMS.get(platform)
    if system not in FEMM_HEADLESS:
        return None
    binary, default = FEMM_HEADLESS[system]
    if root := environ.get('FEMM_DIR'):
        places = [('FEMM_DIR', Path(root)), ('FEMM_DIR', Path(root) / 'bin')]
    else:
        places = [('install dir', Path(default).expanduser())]
    for source, place in places:
        if (place / binary).is_file():
            return source, place / binary
    on_path = which(binary)
    return ('PATH', Path(on_path)) if on_path else None


def femm_smoke(path: Path) -> tuple[bool, str]:
    """``femm_headless -run`` one trivial script (FEMM 5 runs its batch scripts in embedded MicroPython)."""
    with tempfile.TemporaryDirectory(prefix='lab-commons-femm-smoke-') as scratch:
        script = Path(scratch) / 'smoke.py'
        script.write_text('x = 1\n', encoding='utf-8')
        return _run([str(path), '-run', str(script)])


#: Where MATLAB's standard installer puts its launcher, per OS: ``(directory, glob)``; ``R*`` is the release.
MATLAB_INSTALLS: Final = {
    'windows': (('C:/Program Files/MATLAB', 'R*/bin/matlab.exe'),),
    'linux': (('/usr/local/MATLAB', 'R*/bin/matlab'), ('/opt/MATLAB', 'R*/bin/matlab')),
    'macos': (('/Applications', 'MATLAB_R*.app/bin/matlab'),),
}

_RELEASE: Final = re.compile(r'R\d{4}[ab]')


def matlab_find(
    *,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    installs: Mapping[str, Sequence[tuple[str, str]]] = MATLAB_INSTALLS,
) -> tuple[str, Path] | None:
    """MATLAB's launcher: ``matlab`` on ``PATH``, else the newest release under the OS's standard install."""
    if on_path := which('matlab'):
        return 'PATH', Path(on_path).resolve()
    for root, pattern in installs.get(_SYSTEMS.get(platform, ''), ()):
        if found := sorted(Path(root).glob(pattern)):
            return 'install dir', found[-1]
    return None


def matlab_version(path: Path) -> tuple[str, str] | None:
    """The release (``R2024b``) the launcher's install directory is named for."""
    release = next((m.group(0) for part in path.parts if (m := _RELEASE.search(part))), None)
    return ('install path', release) if release else None


def simba_find(
    *, find: Callable[[str], importlib.machinery.ModuleSpec | None] = importlib.util.find_spec
) -> tuple[str, Path] | None:
    """SIMBA's Python API, ``aesim.simba``, as THIS interpreter -- the serving repo's own venv -- imports it."""
    try:
        spec = find('aesim.simba')
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    return 'import', Path(spec.origin or sys.executable)


def simba_version(_path: Path) -> tuple[str, str] | None:
    """The installed ``aesim.simba`` distribution's version."""
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        return 'package metadata', importlib.metadata.version('aesim.simba')
    return None


#: One import, the licence answering, and a tiny transient job -- in this interpreter. SIMBA refuses an
#: EMPTY design ("The design is empty", status ``Error``), so the job is a source across a resistor.
SIMBA_SMOKE: Final = (
    'import aesim.simba as simba\n'
    'assert simba.License.NumberOfAvailableParallelSimulationLicense() > 0, "no licence"\n'
    'design = simba.Design()\n'
    'circuit = design.Circuit\n'
    'v = circuit.AddDevice("DC Voltage Source", 0, 0)\n'
    'r = circuit.AddDevice("Resistor", 10, 0)\n'
    'g = circuit.AddDevice("Ground", 0, 10)\n'
    'circuit.AddConnection(v.P, r.P)\n'
    'circuit.AddConnection(r.N, v.N)\n'
    'circuit.AddConnection(v.N, g.Pin)\n'
    'design.TransientAnalysis.TimeStep = 1e-6\n'
    'design.TransientAnalysis.EndTime = 1e-5\n'
    'job = design.TransientAnalysis.NewJob()\n'
    'status = job.Run()\n'
    'assert str(status) == "OK", job.Summary()\n'
)


def simba_smoke(_path: Path) -> tuple[bool, str]:
    """:data:`SIMBA_SMOKE` in a child of this interpreter, so a crash of the API never takes the tick down."""
    return _run([sys.executable, '-c', SIMBA_SMOKE])


#: JMAG's COM servers: the designer (its executable and version) and the scheduler (reachable = smoke).
JMAG_DESIGNER: Final = 'designer.Application'
JMAG_SCHEDULER: Final = 'scheduler.JobApplication'


def jmag_find() -> tuple[str, Path] | None:
    """The designer's executable, from its COM registration's ``LocalServer32`` (Windows only)."""
    if sys.platform != 'win32':
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'{JMAG_DESIGNER}\\CLSID') as key:
            clsid = winreg.QueryValue(key, None)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'CLSID\\{clsid}\\LocalServer32') as key:
            command = winreg.QueryValue(key, None)
    except OSError:
        return None
    exe = command.strip().split('"')[1] if command.strip().startswith('"') else command.split(' /')[0].strip()
    return ('registry', Path(exe)) if Path(exe).is_file() else None


def jmag_version(path: Path) -> tuple[str, str] | None:
    """The designer executable's file version."""
    found = file_version(path)
    return ('file version', found) if found else None


#: The scheduler answering a COM dispatch is the reachability JMAG's job runs need.
JMAG_SMOKE: Final = f'import win32com.client\nwin32com.client.Dispatch({JMAG_SCHEDULER!r})\n'


def jmag_smoke(_path: Path) -> tuple[bool, str]:
    """The scheduler reachable over COM, in a child of this interpreter."""
    return _run([sys.executable, '-c', JMAG_SMOKE])


def node_find(*, which: Callable[[str], str | None] = shutil.which) -> tuple[str, Path] | None:
    """``node`` on ``PATH``."""
    found = which('node')
    return ('PATH', Path(found)) if found else None


#: Tool -> how it is read. A tool without a check is present where the machine file declares it.
CHECKS: Final[Mapping[str, Check]] = {
    'femm': Check(femm_find, _versioned(lambda p: [str(p), '--version']), femm_smoke),
    'jmag': Check(jmag_find, jmag_version, jmag_smoke),
    'matlab': Check(matlab_find, matlab_version, lambda p: _run([str(p), '-batch', 'ver'])),
    'node': Check(node_find, _versioned(lambda p: [str(p), '--version']), lambda p: _run([str(p), '--version'])),
    'simba': Check(simba_find, simba_version, simba_smoke),
}
