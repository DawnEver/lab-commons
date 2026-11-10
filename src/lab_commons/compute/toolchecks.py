"""HOW EACH VENDOR TOOL IS READ -- find its executable, read its version, check its install is whole.

The readers :mod:`lab_commons.compute.presence` turns into one verdict per tool. Every reader of the OS
is a module function, so a test passes fakes.

A CHECK LAUNCHES NOTHING: every reader reads files, the registry or package metadata, and never starts the
tool. On Windows a vendor executable handed a flag may open a window instead of answering -- Electronics
Desktop Student's ``ansysedtsv.exe -help`` shows a modal help dialog (2026-11-07), and Fluent and others pop
up too -- so an unattended probe blocked until its timeout and read the tool as failed. A check therefore
proves the install is there and whole, never that it runs or that a licence seat is free; seats are what
the host declares. ``tests/test_compute_toolchecks_launch_nothing.py`` holds every :data:`CHECKS` row to it.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.metadata
import importlib.util
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from lab_commons.compute.probe import OS_NAMES

if sys.platform == 'win32':
    import ctypes
    import winreg

__all__ = [
    'ANSYS_ENGINES',
    'CHECKS',
    'LSDYNA_SOLVERS',
    'AnsysEngine',
    'Check',
    'ProbeCrashed',
    'ansys_find',
    'ansys_release',
    'binary_version',
    'femm_find',
    'file_version',
    'installed_smoke',
    'jmag_find',
    'lsdyna_smoke',
    'matlab_find',
    'matlab_version',
    'plain_find',
    'simba_find',
]

_SYSTEMS: Final = OS_NAMES


@dataclass(frozen=True)
class Check:
    """How one tool is read: *find* its executable, read its *version*, *smoke*-check its install; launches nothing.

    *find* returns ``(source, path)`` or ``None``; *version* ``(source, value)`` or ``None``; *smoke*
    ``(passed, detail)``, read off the install, never by running it (the module docstring). *heavy*: a vendor
    solver and its licence seat -- HEAVY COMPUTE, which a light workstation withholds; a plain tool
    (ngspice, node) is not heavy merely because it is probed.
    """

    find: Callable[[], tuple[str, Path] | None]
    version: Callable[[Path], tuple[str, str] | None]
    smoke: Callable[[Path], tuple[bool, str]]
    heavy: bool


# --- the readers ---------------------------------------------------------------------------------------


class ProbeCrashed(RuntimeError):
    """The PROBE failed to read (it measured nothing); not the tool's answer, so it is never cached."""


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


def binary_version(path: Path) -> tuple[str, str] | None:
    """The executable's Windows file version; a binary elsewhere carries none, so ``unread`` (present, unversioned)."""
    found = file_version(path)
    return ('file version', found) if found else ('install', 'unread')


def installed_smoke(path: Path) -> tuple[bool, str]:
    """The install is there: *path* is an executable file. Launches nothing."""
    if path.is_file() and os.access(path, os.X_OK):
        return True, f'{path} is installed and executable'
    return False, f'no executable {path}'


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


def simba_smoke(path: Path) -> tuple[bool, str]:
    """The ``aesim.simba`` package's files are installed -- never imported, so no licence is asked."""
    if path.is_file():
        return True, f'aesim.simba installed at {path}'
    return False, f'no aesim.simba at {path}'


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


def jmag_version(path: Path) -> tuple[str, str] | None:
    """The designer executable's file version."""
    found = file_version(path)
    return ('file version', found) if found else None


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


@dataclass(frozen=True)
class AnsysEngine:
    """Where one Ansys engine lives, per OS.

    *on_path*: globs matched in each ``PATH`` directory; *in_release*: globs under one release root (``.../v251``);
    *elsewhere*: ``(directory, glob)`` installs outside a release root, searched
    last.
    """

    on_path: Mapping[str, tuple[str, ...]]
    in_release: Mapping[str, tuple[str, ...]]
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
    ),
    'fluent': AnsysEngine(
        on_path={'linux': ('fluent',), 'windows': ('fluent.exe',)},
        in_release={
            'linux': ('fluent/bin/fluent',),
            'windows': ('fluent/ntbin/win64/fluent.exe', 'AnsysEM/fluent/ntbin/win64/fluent.exe'),
        },
    ),
    'lsdyna': AnsysEngine(
        on_path={'linux': ('lsdyna',), 'windows': ('lsdyna*.exe',)},
        in_release={'linux': ('ansys/bin/lsdyna',), 'windows': ('ansys/bin/winx64/lsdyna*.exe',)},
        # The standalone LS-DYNA Suite, as motronics ``core/ansys/install.py`` finds it on G.
        elsewhere={'windows': (('C:/Program Files', 'LS-DYNA Suite*/lsdyna/lsdyna_[sd]p.e'),)},
    ),
    'aedt': AnsysEngine(
        on_path={'linux': ('ansysedt',), 'windows': ('ansysedt.exe', 'ansysedtsv.exe')},
        in_release={'linux': ('AnsysEM/ansysedt',), 'windows': ('AnsysEM/ansysedt.exe', 'AnsysEM/ansysedtsv.exe')},
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


#: The solver binaries the LS-DYNA wrapper execs, beside it or in its ``linx64``/``winx64`` directory.
LSDYNA_SOLVERS: Final = ('lsdyna_sp.e', 'lsdyna_dp.e', 'lsdyna_sp.exe', 'lsdyna_dp.exe')


def lsdyna_smoke(path: Path) -> tuple[bool, str]:
    """Passes when a solver binary the wrapper execs exists and is executable -- the install is whole.

    The wrapper would answer no flag licence-free anyway: on Ada (2025R1) it exits 1 for ``-h``, ``-help``,
    ``--help``, ``-v``, ``-version`` and ``help`` alike, because it always execs the licensed
    ``lsdyna_sp.e``, which checks out a seat (MEASURED 2026-11-07). So this smoke proves the
    install is whole, never that a seat is free.
    """
    places = (path.parent, path.parent / 'linx64', path.parent / 'winx64')
    for exe in (place / name for place in places for name in LSDYNA_SOLVERS):
        if exe.is_file() and os.access(exe, os.X_OK):
            return True, f'solver binary {exe} is executable'
    return False, f'no executable {"/".join(LSDYNA_SOLVERS)} beside {path} or in its linx64/winx64'


#: Tool -> how it is read. A tool without a check is present where the machine file declares it.
CHECKS: Final[Mapping[str, Check]] = {
    'femm': Check(femm_find, binary_version, installed_smoke, heavy=True),
    'jmag': Check(jmag_find, jmag_version, installed_smoke, heavy=True),
    'matlab': Check(matlab_find, matlab_version, installed_smoke, heavy=True),
    'simba': Check(simba_find, simba_version, simba_smoke, heavy=True),
    'ngspice': Check(plain_find('ngspice'), binary_version, installed_smoke, heavy=False),
    **{
        engine: Check(
            ansys_find(engine), ansys_release, lsdyna_smoke if engine == 'lsdyna' else installed_smoke, heavy=True
        )
        for engine in ANSYS_ENGINES
    },
    'node': Check(plain_find('node'), binary_version, installed_smoke, heavy=False),
}
