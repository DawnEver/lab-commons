"""IS A VENDOR TOOL REALLY THERE -- several sources, one verdict, with its evidence.

A tool is PRESENT on a host only when all four agree:

1. its executable is FOUND -- a declared path (an environment variable the tool's own docs name), a
   standard install directory, the registry or ``PATH``;
2. its VERSION reads (``--version``, the executable's file version, its install path, its package
   metadata) and EQUALS the version the serving repo declares ONCE for the family, in its
   ``pyproject.toml`` ``[tool.lab_commons.tools]`` (``<tool> = "<version>"``) -- vendor engines are
   uniform by policy, so a host is never the place a version is declared; none declared is absent;
3. a SMOKE run passes -- the tool does one trivial thing end to end (:data:`CHECKS`);
4. no real unit on that tool has failed on this host since that smoke (:func:`demote`).

Each step leaves :class:`Evidence` (source, value, time), and the host card shows it
(:mod:`lab_commons.dev.forgehosts`): the card is the probe's reading, never a declaration. Seat counts
are the one thing a person declares (:mod:`lab_commons.box.machine`), because no probe can read a licence.

A smoke costs seconds to minutes (MATLAB starting), so a reading is CACHED for :data:`TTL` in
``tool-presence.json`` beside the machine file. :func:`refresh` re-probes what is due -- older than the
TTL, or demoted since its smoke -- and ``forge serve`` calls it once per tick; :func:`cached` only reads,
and a reading past its TTL is absent, so a host nobody refreshes stops claiming. All readers of the OS
are module functions behind :class:`Check`, so a test passes fakes.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from lab_commons.config import config_path

if sys.platform == 'win32':
    import ctypes
    import winreg

__all__ = [
    'CHECKS',
    'TTL',
    'Check',
    'Evidence',
    'Presence',
    'cached',
    'declared_versions',
    'demote',
    'probe',
    'refresh',
]

#: How long one probe's reading holds before :func:`refresh` runs it again and :func:`cached` reads it absent.
TTL: Final = timedelta(hours=6)

#: The longest one smoke may run (MATLAB's first start on a cold box is the slow one).
SMOKE_TIMEOUT_S: Final = 300.0

_SYSTEMS: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}


@dataclass(frozen=True)
class Evidence:
    """One reading: which source said it, what it said, and when (ISO 8601, UTC)."""

    source: str
    value: str
    time: str


@dataclass(frozen=True)
class Presence:
    """One tool on this host: the verdict and every reading it rests on, in probe order."""

    tool: str
    present: bool
    evidence: tuple[Evidence, ...] = ()

    def row(self) -> dict[str, Any]:
        """The card's form: ``{present, evidence: [{source, value, time}, ...]}``."""
        return {'present': self.present, 'evidence': [asdict(one) for one in self.evidence]}

    @classmethod
    def of_row(cls, tool: str, row: Mapping[str, Any]) -> Presence:
        """The inverse of :meth:`row`."""
        return cls(tool, bool(row['present']), tuple(Evidence(**one) for one in row.get('evidence', ())))


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


def simba_find(*, find: Callable[[str], Any] = importlib.util.find_spec) -> tuple[str, Path] | None:
    """SIMBA's Python API, ``aesim.simba``, as THIS interpreter -- the serving repo's own venv -- imports it."""
    try:
        spec = find('aesim.simba')
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    return 'import', Path(getattr(spec, 'origin', None) or sys.executable)


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


# --- the verdict ---------------------------------------------------------------------------------------


def declared_versions(root: Path) -> dict[str, str]:
    """The serving repo's ``[tool.lab_commons.tools]``: ``{tool: version}``; a non-string version is refused."""
    manifest = root / 'pyproject.toml'
    if not manifest.is_file():
        return {}
    table = tomllib.loads(manifest.read_text(encoding='utf-8')).get('tool', {}).get('lab_commons', {}).get('tools', {})
    if bad := sorted(name for name, version in table.items() if not isinstance(version, str)):
        msg = f'{manifest} [tool.lab_commons.tools]: {bad} must each be a version string, e.g. femm = "5.0"'
        raise ValueError(msg)
    return dict(table)


def probe(tool: str, check: Check, declared: str | None, *, now: datetime) -> Presence:
    """Steps 1-3 for one tool, stopping at the first that fails; every reading becomes evidence."""
    at = now.isoformat()
    found = check.find()
    if found is None:
        return Presence(tool, present=False, evidence=(Evidence('find', 'not found', at),))
    source, path = found
    seen = [Evidence(f'find: {source}', str(path), at)]
    version = check.version(path)
    if version is None:
        return Presence(tool, present=False, evidence=(*seen, Evidence('version', 'unreadable', at)))
    seen.append(Evidence(f'version: {version[0]}', version[1], at))
    if declared is None:
        return Presence(
            tool, present=False, evidence=(*seen, Evidence('version', 'none declared in [tool.lab_commons.tools]', at))
        )
    if not _matches(version[1], declared):
        return Presence(tool, present=False, evidence=(*seen, Evidence('version', f'declared {declared}', at)))
    passed, detail = check.smoke(path)
    return Presence(
        tool, present=passed, evidence=(*seen, Evidence('smoke', f'{"passed" if passed else "FAILED"}: {detail}', at))
    )


def _matches(read: str, declared: str) -> bool:
    """The read version names the declared one: equal, or a whole token of it (a leading ``v`` dropped)."""
    tokens = {token.removeprefix('v') for token in re.split(r'[^\w.]+', read)}
    return read == declared or declared in tokens


def _state(path: Path | None) -> Path:
    return path or config_path().parent / 'tool-presence.json'


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=1), encoding='utf-8')
    temp.replace(path)


def _current(entry: Mapping[str, Any], now: datetime, ttl: timedelta) -> bool:
    """A reading still holds: inside its TTL and no real unit failed since it was taken."""
    probed = datetime.fromisoformat(entry['probed'])
    failed = entry.get('failed')
    return now - probed < ttl and (failed is None or datetime.fromisoformat(failed) < probed)


def _verdict(tool: str, entry: Mapping[str, Any], now: datetime, ttl: timedelta) -> Presence:
    one = Presence.of_row(tool, entry)
    if entry.get('failed') and datetime.fromisoformat(entry['failed']) >= datetime.fromisoformat(entry['probed']):
        return Presence(
            tool, present=False, evidence=(*one.evidence, Evidence('failure', entry.get('why', ''), entry['failed']))
        )
    if now - datetime.fromisoformat(entry['probed']) >= ttl:
        return Presence(
            tool, present=False, evidence=(*one.evidence, Evidence('ttl', f'older than {ttl}', now.isoformat()))
        )
    return one


def cached(
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> dict[str, Presence]:
    """Every checked tool's last reading, read only: never probed is absent, and so is stale or demoted."""
    now = now or datetime.now(UTC)
    stored = _load(_state(state))
    return {
        tool: _verdict(tool, stored[tool], now, ttl)
        if tool in stored
        else Presence(tool, present=False, evidence=(Evidence('probe', 'never run on this host', now.isoformat()),))
        for tool in checks
    }


def refresh(
    versions: Mapping[str, str],
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> dict[str, Presence]:
    """Re-probe every tool whose reading is due (stale, demoted or missing) against *versions*; then :func:`cached`."""
    now = now or datetime.now(UTC)
    path = _state(state)
    stored = _load(path)
    due = [tool for tool in checks if tool not in stored or not _current(stored[tool], now, ttl)]
    for tool in due:
        stored[tool] = {**probe(tool, checks[tool], versions.get(tool), now=now).row(), 'probed': now.isoformat()}
    if due:
        _save(path, stored)
    return cached(checks=checks, state=path, now=now, ttl=ttl)


def demote(tools: Sequence[str], why: str, *, state: Path | None = None, now: datetime | None = None) -> None:
    """A real unit on *tools* failed here: each is absent until :func:`refresh` sees its smoke pass again."""
    path = _state(state)
    stored = _load(path)
    at = (now or datetime.now(UTC)).isoformat()
    hit = [tool for tool in tools if tool in stored]
    for tool in hit:
        stored[tool] = {**stored[tool], 'failed': at, 'why': why}
    if hit:
        _save(path, stored)
