"""HOW EACH VENDOR TOOL IS READ -- find its executable, read its version, check its install is whole.

The readers :mod:`lab_commons.compute.presence` turns into one verdict per tool. Every reader of the OS
is a module function, so a test passes fakes.

A CHECK LAUNCHES NOTHING: every reader reads files, the registry or package metadata, and never starts the
tool. On Windows a vendor executable handed a flag may open a window instead of answering -- Electronics
Desktop Student's ``ansysedtsv.exe -help`` shows a modal help dialog (2026-11-07), and Fluent and others pop
up too -- so an unattended probe blocked until its timeout and read the tool as failed. A check therefore
proves the install is there and whole, never that it runs or that a license seat is free; seats are what
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
from lab_commons.tools.managed import installed

if sys.platform == 'win32':
    import ctypes
    import winreg

__all__ = [
    'ANSYS_ENGINES',
    'CHECKS',
    'FEMM_HEADLESS',
    'FEMM_LEGACY_HOMES',
    'FEMM_VERSION_FILE',
    'INSTALL_DOOR',
    'LSDYNA_SOLVERS',
    'VERSION_FILES',
    'AnsysEngine',
    'Check',
    'ProbeCrashed',
    'Unmanaged',
    'VersionUnreadable',
    'ansys_find',
    'ansys_release',
    'ansys_within',
    'binary_version',
    'femm_find',
    'femm_release',
    'femm_version',
    'file_version',
    'installed_smoke',
    'jmag_find',
    'jmag_within',
    'lsdyna_smoke',
    'managed_find',
    'matlab_find',
    'matlab_version',
    'matlab_within',
    'plain_find',
    'simba_find',
    'unmanaged',
]

_SYSTEMS: Final = OS_NAMES


@dataclass(frozen=True)
class Check:
    """How one tool is read: *find* its executable, read its *version*, *smoke*-check its install; launches nothing.

    *find* returns ``(source, path)`` or ``None``; *version* ``(source, value)`` or ``None``; *smoke*
    ``(passed, detail)``, read off the install, never by running it (the module docstring). *heavy*: a vendor
    solver and its license seat -- HEAVY COMPUTE, which a light workstation withholds; a plain tool
    (ngspice, node) is not heavy merely because it is probed. *within*: the executable under an install home a
    machine DECLARES (a user-installed tool's ``[dev.vendors.<vendor>] home``, :mod:`lab_commons.tools.vendor`),
    ``(source, path)`` or ``None`` -- the same layout *find* searches, rooted where the machine says.
    """

    find: Callable[[], tuple[str, Path] | None]
    version: Callable[[Path], tuple[str, str] | None]
    smoke: Callable[[Path], tuple[bool, str]]
    heavy: bool
    within: Callable[[Path], tuple[str, Path] | None] | None = None


# --- the readers ---------------------------------------------------------------------------------------


class ProbeCrashed(RuntimeError):
    """The PROBE failed to read (it measured nothing); not the tool's answer, so it is never cached."""


class VersionUnreadable(Exception):
    """The tool is found but its install states no version; the message names what was read and the door.

    A *version* reader raises it where a missing version is a defect of the install rather than of the
    tool (FEMM). The probe records it as the reading, so the tool is absent with its cause on the card.
    """


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


class Unmanaged(Exception):
    """The tool is installed OUTSIDE the managed tool root (:mod:`lab_commons.tools`); the message names the door.

    A *find* reader raises it for an install it may not accept (user ruling 2026-11-25: every tool goes under the
    family-managed root, never a hand install). The probe records it as the reading: absent, with its cause.
    """


#: The door every tool is installed through: ``<INSTALL_DOOR> <tool>`` (:mod:`lab_commons.tools`).
INSTALL_DOOR: Final = 'python -m lab_commons.tools install'


def managed_find(tool: str) -> tuple[str, Path] | None:
    """*tool*'s binary as the managed root records it (``<home>/tools/<tool>/<version>/...``), when it is there.

    Raises:
        Unmanaged: the record is unreadable as one; the message names the door that rewrites it.

    """
    try:
        one = installed(tool)
    except ValueError as unread:
        msg = f'{unread}: reinstall through the door: {INSTALL_DOOR} {tool}'
        raise Unmanaged(msg) from None
    return ('managed', one.binary) if one is not None and one.binary.is_file() else None


def unmanaged(tool: str, path: Path, where: str) -> Unmanaged:
    """The refusal of an install found by hand at *path* (*where*: how it was found), naming the door."""
    door = f'{INSTALL_DOOR} {tool}'
    return Unmanaged(
        f'unmanaged: {path} ({where}) is outside the managed tool root; reinstall through the door: {door}'
    )


#: The FEMM 5 headless engine's binary per OS.
FEMM_HEADLESS: Final = {'linux': 'femm_headless', 'windows': 'femm_headlessw.exe'}

#: Where FEMM 5 was put by hand before the managed root (user ruling 2026-11-25): the old one tool root
#: ``~/.local/opt/femm-qt`` (ruling 2026-11-06) and the Windows installer's ``C:/femm-qt``. An install found
#: there is UNMANAGED -- reported with the door, never accepted.
FEMM_LEGACY_HOMES: Final = {
    'linux': ('~/.local/opt/femm-qt/bin',),
    'windows': ('~/.local/opt/femm-qt/bin', 'C:/femm-qt/bin'),
}


#: The FEMM 5 install's VERSION FILE, beside the binary: femm-qt's own build stamp, which its
#: ``cmake/BuildInfo.cmake`` writes to ``<build>/generated/version.env`` -- ``#`` comment lines and
#: ``KEY=VALUE`` lines, ``FEMM_VERSION=5.0.YYYYMMDD`` among them, femm-qt's ONE version number (its
#: ``project(VERSION)``, the RELEASE date). Installing femm_headless copies it beside the binary. Read where the
#: binary carries no file version (Linux), so the version is measured there too (user ruling 2026-11-25).
FEMM_VERSION_FILE: Final = 'version.env'

#: The version file's key holding the version, and the shape its value must have.
_FEMM_VERSION_KEY: Final = 'FEMM_VERSION'
_DOTTED: Final = re.compile(r'\d+(\.\d+)+')

#: Tool -> the version file its install carries beside its binary, and the key holding the version: what the
#: install door writes where an installer ships none (:mod:`lab_commons.tools.install`).
VERSION_FILES: Final = {'femm': (FEMM_VERSION_FILE, _FEMM_VERSION_KEY)}


def _femm_door(file: Path) -> str:
    """The door out of a missing or unreadable version file: what the install must carry."""
    return (
        f'reinstall femm_headless through `{INSTALL_DOOR} femm` so its install carries {file} '
        f'(the femm-qt build stamp generated/{FEMM_VERSION_FILE}, beside the binary)'
    )


def femm_release(fixed: str) -> str:
    """femm-qt's one version number from a Windows file version: ``5.0.2026.1112`` -> ``5.0.20261112``.

    femm-qt's ``cmake/WinVersion.cmake`` fits ``5.0.YYYYMMDD`` into four 16-bit fields by splitting the
    8-digit patch into ``YYYY`` and ``MMDD``; any other patch sits in field three with field four ``0``.
    """
    major, minor, high, low = fixed.split('.')
    return f'{major}.{minor}.{high}{int(low):04d}' if int(low) else f'{major}.{minor}.{high}'


def femm_version(path: Path, *, platform: str = sys.platform) -> tuple[str, str]:
    """FEMM 5's version, MEASURED: the binary's file version on Windows, else the install's version file.

    Raises:
        VersionUnreadable: no file version (Windows), or no version file or no ``FEMM_VERSION`` in it.

    """
    if _SYSTEMS.get(platform) == 'windows':
        found = file_version(path)
        if found is None:
            msg = f'{path} carries no file version: reinstall femm_headless through `{INSTALL_DOOR} femm`'
            raise VersionUnreadable(msg)
        return 'file version', femm_release(found)
    file = path.parent / FEMM_VERSION_FILE
    try:
        text = file.read_text(encoding='utf-8')
    except FileNotFoundError:
        msg = f'{file} is missing: {_femm_door(file)}'
        raise VersionUnreadable(msg) from None
    except (OSError, UnicodeDecodeError) as exc:
        msg = f'{file} is unreadable ({exc}): {_femm_door(file)}'
        raise VersionUnreadable(msg) from None
    rows = (line.partition('=') for line in text.splitlines() if not line.lstrip().startswith('#'))
    value = next((v.strip() for k, _eq, v in rows if k.strip() == _FEMM_VERSION_KEY), '')
    if not _DOTTED.fullmatch(value):
        msg = f'{file} states no {_FEMM_VERSION_KEY}=<a.b.c>: {_femm_door(file)}'
        raise VersionUnreadable(msg)
    return 'version file', value


def femm_find(
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, Path] | None:
    """The FEMM 5 headless binary: the MANAGED install first, else ``FEMM_DIR`` (or its ``bin``).

    ``FEMM_DIR`` is an explicit declaration (a person's, or a job file's setup line), so it is read as given.
    A binary found anywhere else -- a legacy home (:data:`FEMM_LEGACY_HOMES`) or ``PATH`` -- is a hand install.

    Raises:
        Unmanaged: FEMM is found only in a legacy home or on ``PATH``; the message names the install door.

    """
    system = _SYSTEMS.get(platform)
    if system not in FEMM_HEADLESS:
        return None
    tool = 'femm'
    if found := managed_find(tool):
        return found
    binary = FEMM_HEADLESS[system]
    if root := environ.get('FEMM_DIR'):
        for place in (Path(root), Path(root) / 'bin'):
            if (place / binary).is_file():
                return 'FEMM_DIR', place / binary
    for home in FEMM_LEGACY_HOMES[system]:
        if (exe := Path(home).expanduser() / binary).is_file():
            raise unmanaged(tool, exe, 'a legacy home')
    if on_path := which(binary):
        raise unmanaged(tool, Path(on_path), 'on PATH')
    return None


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


def matlab_within(*, platform: str = sys.platform) -> Callable[[Path], tuple[str, Path] | None]:
    """MATLAB's launcher under a declared release home (``.../R2024b``): the standard layout below its ``R*``."""

    def within(home: Path) -> tuple[str, Path] | None:
        for _root, pattern in MATLAB_INSTALLS.get(_SYSTEMS.get(platform, ''), ()):
            if (exe := home / pattern.split('/', 1)[1]).is_file():
                return 'declared home', exe
        return None

    return within


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
    """The ``aesim.simba`` package's files are installed -- never imported, so no license is asked."""
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


def jmag_within(home: Path) -> tuple[str, Path] | None:
    """The designer's executable in a declared JMAG-Designer home."""
    exe = home / 'designer.exe'
    return ('declared home', exe) if exe.is_file() else None


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


def ansys_within(engine: str, *, platform: str = sys.platform) -> Callable[[Path], tuple[str, Path] | None]:
    """One Ansys engine under a declared home: a release directory ``v<NNN>``, else the newest one under it."""
    spec = ANSYS_ENGINES[engine]

    def within(home: Path) -> tuple[str, Path] | None:
        system = _SYSTEMS.get(platform, '')
        if _RELEASE_DIR.match(home.name):
            roots = [home]
        else:
            found = [d for d in home.glob('v*') if _RELEASE_DIR.match(d.name) and d.is_dir()]
            roots = sorted(found, key=lambda d: d.name, reverse=True)
        for root in roots:
            for pattern in spec.in_release.get(system, ()):
                if hits := sorted(p for p in root.glob(pattern) if p.is_file()):
                    return 'declared home', hits[-1]
        return None

    return within


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

    The wrapper would answer no flag license-free anyway: on Ada (2025R1) it exits 1 for ``-h``, ``-help``,
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
    'femm': Check(femm_find, femm_version, installed_smoke, heavy=True),
    'jmag': Check(jmag_find, jmag_version, installed_smoke, heavy=True, within=jmag_within),
    'matlab': Check(matlab_find, matlab_version, installed_smoke, heavy=True, within=matlab_within()),
    'simba': Check(simba_find, simba_version, simba_smoke, heavy=True),
    'ngspice': Check(plain_find('ngspice'), binary_version, installed_smoke, heavy=False),
    **{
        engine: Check(
            ansys_find(engine),
            ansys_release,
            lsdyna_smoke if engine == 'lsdyna' else installed_smoke,
            heavy=True,
            within=ansys_within(engine),
        )
        for engine in ANSYS_ENGINES
    },
    'node': Check(plain_find('node'), binary_version, installed_smoke, heavy=False),
}
