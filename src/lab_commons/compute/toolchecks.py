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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons.compute.probe import OS_NAMES

if sys.platform == 'win32':
    import ctypes
    import winreg

__all__ = [
    'ANSYS_ENGINES',
    'CHECKS',
    'LSDYNA_SOLVERS',
    'SIMBA_SMOKE',
    'AnsysEngine',
    'Check',
    'ProbeCrashed',
    'ansys_find',
    'ansys_release',
    'ansys_smoke',
    'femm_find',
    'file_version',
    'jmag_find',
    'jmag_headless',
    'lsdyna_smoke',
    'matlab_find',
    'matlab_version',
    'plain_find',
    'plain_smoke',
    'simba_find',
]

#: The longest one smoke may run (MATLAB's first start on a cold box is the slow one).
SMOKE_TIMEOUT_S: Final = 300.0

_SYSTEMS: Final = OS_NAMES


@dataclass(frozen=True)
class Check:
    """How one tool is read: *find* its executable, read its *version*, run its *smoke*.

    *find* returns ``(source, path)`` or ``None``; *version* ``(source, value)`` or ``None``; *smoke*
    ``(passed, detail)``. *heavy*: a vendor solver and its licence seat -- HEAVY COMPUTE, which a light workstation
    withholds; a plain tool (ngspice, node) is not heavy merely because it is probed.
    """

    find: Callable[[], tuple[str, Path] | None]
    version: Callable[[Path], tuple[str, str] | None]
    smoke: Callable[[Path], tuple[bool, str]]
    heavy: bool


# --- the readers ---------------------------------------------------------------------------------------


class ProbeCrashed(RuntimeError):
    """The PROBE failed to run its child (it measured nothing); not the tool's answer, so it is never cached."""


def _probe(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """THE ONE PLACE A PROBE STARTS A CHILD, with no stdin.

    A probe never reads stdin, so the child gets none: from a hidden
    detached serve the inherited stdin is invalid and the start raised ``[WinError 6]`` (2026-10-20).

    Raises:
        ProbeCrashed: the child could not be started -- the probe's own error.
        subprocess.TimeoutExpired: the child ran and did not answer -- the tool's answer.

    """
    try:
        return _door.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            check=False,
        )
    except OSError as failed:
        msg = f'{type(failed).__name__}: {failed}'
        raise ProbeCrashed(msg) from failed


def _run(argv: Sequence[str], timeout: float = SMOKE_TIMEOUT_S) -> tuple[bool, str]:
    """Run *argv*; ``(exit 0, the last line of its output or why it did not answer)``."""
    try:
        done = _probe(argv, timeout)
    except subprocess.TimeoutExpired as failed:
        return False, f'{type(failed).__name__}: {failed}'
    lines = [line.strip() for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return done.returncode == 0, f'exit {done.returncode}: {lines[-1] if lines else "no output"}'


def _first_line(argv: Sequence[str]) -> str | None:
    try:
        done = _probe(argv, 60)
    except subprocess.TimeoutExpired:
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


#: The FEMM 5 headless engine per OS: its binary and the install directory used when ``FEMM_DIR`` is unset --
#: the one tool root ``~/.local/opt/<tool>`` on every OS (user ruling 2026-11-06).
FEMM_HEADLESS: Final = {
    'linux': ('femm_headless', '~/.local/opt/femm-qt/bin'),
    'windows': ('femm_headlessw.exe', '~/.local/opt/femm-qt/bin'),
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


#: JMAG's COM registration -- read only to FIND the designer's executable; the probe never dispatches it.
JMAG_DESIGNER: Final = 'designer.Application'


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


#: Run inside Designer's own python by ``designer.exe -w`` (no window, no COM): the in-process Application
#: writes its version string (``25.2.01zp``) to the file ``{out!r}`` names, so the file IS the verdict --
#: Designer exits 0 even when its script raised.
JMAG_SMOKE: Final = (
    'import designer\n'
    'version = designer.GetApplication().VersionString(True)\n'
    'with open({out!r}, "w", encoding="utf-8") as f:\n'
    '    f.write(version)\n'
)

#: The last headless reading per executable, so one probe's version and smoke share one Designer start.
_JMAG_READ: dict[Path, tuple[str | None, str]] = {}


def jmag_headless(path: Path) -> tuple[str | None, str]:
    """``designer.exe -w -l <log> <script>``: ``(the version the tool wrote, or None; what happened)``."""
    with tempfile.TemporaryDirectory(prefix='lab-commons-jmag-smoke-') as scratch:
        out, script = Path(scratch) / 'version.txt', Path(scratch) / 'smoke.py'
        script.write_text(JMAG_SMOKE.format(out=str(out)), encoding='utf-8')
        _exit, detail = _run([str(path), '-w', '-l', str(Path(scratch) / 'designer.log'), str(script)])
        version = out.read_text(encoding='utf-8').strip() if out.is_file() else ''
    if not version:
        return None, f'{detail}; the script wrote no version file'
    return version, f'{detail}; wrote version {version}'


def jmag_version(path: Path) -> tuple[str, str] | None:
    """The version Designer itself reports, headless; else the executable's file version (the smoke says why)."""
    _JMAG_READ[path] = read = jmag_headless(path)
    if read[0] is not None:
        return 'designer -w', read[0]
    found = file_version(path)
    return ('file version', found) if found else None


def jmag_smoke(path: Path) -> tuple[bool, str]:
    """Designer ran a script headless and it wrote its version -- the route a JMAG bundle runs by."""
    version, detail = _JMAG_READ.pop(path, None) or jmag_headless(path)
    return version is not None, detail


#: The longest a plain tool may take to answer its ``--version``.
VERSION_TIMEOUT_S: Final = 30.0

#: A plain tool's console binary per OS (Windows' ``ngspice.exe`` is the GUI; ``ngspice_con`` is its console).
_BINARIES: Final = {
    'ngspice': {'windows': 'ngspice_con'},
    'node': {},
}

#: A plain tool's standard install directories per OS, searched when ``PATH`` does not carry it.
_INSTALL_DIRS: Final = {
    'ngspice': {'windows': ('C:/Spice64/bin',)},
}


def plain_find(tool: str, *, platform: str = sys.platform) -> Callable[[], tuple[str, Path] | None]:
    """A plain (unlicensed) tool's console binary: on ``PATH``, else in a standard install directory."""

    def find() -> tuple[str, Path] | None:
        system = _SYSTEMS.get(platform, '')
        binary = _BINARIES[tool].get(system, tool)
        if on_path := shutil.which(binary):
            return 'PATH', Path(on_path)
        for place in _INSTALL_DIRS.get(tool, {}).get(system, ()):
            for exe in (Path(place) / binary, Path(place) / f'{binary}.exe'):
                if exe.is_file():
                    return 'install dir', exe
        return None

    return find


def plain_smoke(path: Path) -> tuple[bool, str]:
    """The tool answers ``--version`` with exit 0 within :data:`VERSION_TIMEOUT_S`."""
    return _run([str(path), '--version'], VERSION_TIMEOUT_S)


@dataclass(frozen=True)
class AnsysEngine:
    """Where one Ansys engine lives, per OS, and the flag its licence-free start answers.

    *on_path*: globs matched in each ``PATH`` directory; *in_release*: globs under one release root (``.../v251``);
    *smoke*: the flag the smoke passes; *elsewhere*: ``(directory, glob)`` installs outside a release root, searched
    last.
    """

    on_path: Mapping[str, tuple[str, ...]]
    in_release: Mapping[str, tuple[str, ...]]
    smoke: tuple[str, ...]
    elsewhere: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


#: The roots release directories ``v<NNN>`` sit under when no ``AWP_ROOT<NNN>`` names one: the full install, then the
#: Student one (Windows); the conventional Linux roots. Same places as motronics ``core/ansys/install.py``.
ANSYS_INSTALLS: Final = {
    'windows': ('C:/Program Files/ANSYS Inc', 'C:/Program Files/ANSYS Inc/ANSYS Student'),
    'linux': ('/usr/ansys_inc', '/ansys_inc'),
}

#: The four engines, named as the serving repo's resources. MAPDL on Windows is ``ANSYS<NNN>.exe`` (Student:
#: ``MAPDL.exe``); a Student AEDT is ``ansysedtsv`` and its working Fluent is the one inside ``AnsysEM`` (MEASURED on
#: G 2026-11-05 by motronics: Student's standalone Fluent lacks its DLLs). A cluster ``module load`` puts
#: ``mapdl``/``ansys<NNN>``/``fluent``/``lsdyna`` on ``PATH`` and no ``ansysedt``.
ANSYS_ENGINES: Final[Mapping[str, AnsysEngine]] = {
    'mapdl': AnsysEngine(
        on_path={'linux': ('mapdl', 'ansys[0-9][0-9][0-9]'), 'windows': ('ANSYS[0-9][0-9][0-9].exe', 'MAPDL.exe')},
        in_release={
            'linux': ('ansys/bin/mapdl', 'ansys/bin/ansys[0-9][0-9][0-9]'),
            'windows': ('ansys/bin/winx64/ANSYS[0-9][0-9][0-9].exe', 'ansys/bin/winx64/MAPDL.exe'),
        },
        smoke=('-v',),
    ),
    'fluent': AnsysEngine(
        on_path={'linux': ('fluent',), 'windows': ('fluent.exe',)},
        in_release={
            'linux': ('fluent/bin/fluent',),
            'windows': ('fluent/ntbin/win64/fluent.exe', 'AnsysEM/fluent/ntbin/win64/fluent.exe'),
        },
        smoke=('-help',),
    ),
    'lsdyna': AnsysEngine(
        on_path={'linux': ('lsdyna',), 'windows': ('lsdyna*.exe',)},
        in_release={'linux': ('ansys/bin/lsdyna',), 'windows': ('ansys/bin/winx64/lsdyna*.exe',)},
        smoke=(),
        # The standalone LS-DYNA Suite, as motronics ``core/ansys/install.py`` finds it on G.
        elsewhere={'windows': (('C:/Program Files', 'LS-DYNA Suite*/lsdyna/lsdyna_[sd]p.e'),)},
    ),
    'aedt': AnsysEngine(
        on_path={'linux': ('ansysedt',), 'windows': ('ansysedt.exe', 'ansysedtsv.exe')},
        in_release={'linux': ('AnsysEM/ansysedt',), 'windows': ('AnsysEM/ansysedt.exe', 'AnsysEM/ansysedtsv.exe')},
        smoke=('-help',),
    ),
}

_RELEASE_DIR: Final = re.compile(r'^v(\d\d)(\d)$')
_AWP_ROOT: Final = re.compile(r'^AWP_ROOT(\d{3})$')


def _release_roots(system: str, environ: Mapping[str, str]) -> list[tuple[str, Path]]:
    """Every release root, newest first: each ``AWP_ROOT<NNN>``, then each ``v<NNN>`` under the standard installs."""
    named = sorted(((m.group(1), v) for k, v in environ.items() if (m := _AWP_ROOT.match(k))), reverse=True)
    roots = [(f'AWP_ROOT{ver}', Path(value)) for ver, value in named]
    for base in ANSYS_INSTALLS.get(system, ()):
        found = [d for d in Path(base).glob('v*') if _RELEASE_DIR.match(d.name) and d.is_dir()]
        roots += [('install dir', d) for d in sorted(found, key=lambda d: d.name, reverse=True)]
    return roots


def ansys_find(
    engine: str, *, platform: str = sys.platform, environ: Mapping[str, str] = os.environ
) -> Callable[[], tuple[str, Path] | None]:
    """One Ansys engine: on ``PATH`` (a cluster module), else an ``AWP_ROOT<NNN>``, else the newest standard release.

    A file under a directory not spelled ``v<NNN>`` is never a release.
    """
    spec = ANSYS_ENGINES[engine]

    def find() -> tuple[str, Path] | None:
        system = _SYSTEMS.get(platform, '')
        for place in filter(None, environ.get('PATH', '').split(os.pathsep)):
            for pattern in spec.on_path.get(system, ()):
                if hits := sorted(p for p in Path(place).glob(pattern) if p.is_file()):
                    return 'PATH', hits[-1]
        for source, root in _release_roots(system, environ):
            for pattern in spec.in_release.get(system, ()):
                if hits := sorted(p for p in root.glob(pattern) if p.is_file()):
                    return source, hits[-1]
        for base, pattern in spec.elsewhere.get(system, ()):
            if hits := sorted(p for p in Path(base).glob(pattern) if p.is_file()):
                return 'install dir', hits[-1]
        return None

    return find


def ansys_release(path: Path) -> tuple[str, str] | None:
    """The release the path names: ``v<NNN>`` (``v251`` -> ``2025 R1``, innermost wins), else a Suite's ``R16.1``."""
    found = [m for part in Path(path).parts if (m := _RELEASE_DIR.match(part))]
    if found:
        return 'install path', f'20{found[-1].group(1)} R{found[-1].group(2)}'
    suite = re.findall(r'R(\d+\.\d+)', Path(path).as_posix())
    return ('install path', f'R{suite[-1]}') if suite else None


def ansys_smoke(engine: str) -> Callable[[Path], tuple[bool, str]]:
    """The binary STARTS: its version/help flag exits 0 within :data:`VERSION_TIMEOUT_S`.

    A real solve needs a licence seat, so the smoke never solves -- it proves the install loads (no missing DLL or
    library), not that a seat is free; seats are what the host declares.
    """
    flags = ANSYS_ENGINES[engine].smoke
    return lambda path: _run([str(path), *flags], VERSION_TIMEOUT_S)


#: The solver binaries the LS-DYNA wrapper execs, beside it or in its ``linx64``/``winx64`` directory.
LSDYNA_SOLVERS: Final = ('lsdyna_sp.e', 'lsdyna_dp.e', 'lsdyna_sp.exe', 'lsdyna_dp.exe')


def lsdyna_smoke(path: Path) -> tuple[bool, str]:
    """RUNS NOTHING: passes when a solver binary the wrapper execs exists and is executable.

    The wrapper answers no flag licence-free: on Ada (2025R1) it exits 1 for ``-h``, ``-help``, ``--help``, ``-v``,
    ``-version`` and ``help`` alike, because it always execs the licensed ``lsdyna_sp.e``, which checks out a seat
    (MEASURED 2026-11-07). So this smoke proves the install is whole, never that a seat is free.
    """
    places = (path.parent, path.parent / 'linx64', path.parent / 'winx64')
    for exe in (place / name for place in places for name in LSDYNA_SOLVERS):
        if exe.is_file() and os.access(exe, os.X_OK):
            return True, f'solver binary {exe} is executable'
    return False, f'no executable {"/".join(LSDYNA_SOLVERS)} beside {path} or in its linx64/winx64'


#: Tool -> how it is read. A tool without a check is present where the machine file declares it.
CHECKS: Final[Mapping[str, Check]] = {
    'femm': Check(femm_find, _versioned(lambda p: [str(p), '--version']), femm_smoke, heavy=True),
    'jmag': Check(jmag_find, jmag_version, jmag_smoke, heavy=True),
    'matlab': Check(matlab_find, matlab_version, lambda p: _run([str(p), '-batch', 'ver']), heavy=True),
    'simba': Check(simba_find, simba_version, simba_smoke, heavy=True),
    'ngspice': Check(plain_find('ngspice'), _versioned(lambda p: [str(p), '--version']), plain_smoke, heavy=False),
    **{
        engine: Check(
            ansys_find(engine), ansys_release, lsdyna_smoke if engine == 'lsdyna' else ansys_smoke(engine), heavy=True
        )
        for engine in ANSYS_ENGINES
    },
    'node': Check(plain_find('node'), _versioned(lambda p: [str(p), '--version']), plain_smoke, heavy=False),
}
