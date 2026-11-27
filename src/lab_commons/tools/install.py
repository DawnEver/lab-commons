"""INSTALL ONE RELEASE under the managed tool root: staged, hash-verified, versioned, probed, then recorded.

The steps, in order -- each failure is a refusal naming its cause and its door, and leaves the recorded install
as it was:

1. SKIP: the release already recorded (:mod:`lab_commons.tools.managed`) and passing its probe is skipped by name.
2. STAGE: a fresh EMPTY directory ``<tool root>/.staging/<tool>-<version>-<stamp>/`` receives the download (or a
   caller's local copy, ``--installer``). Nothing else is in it, so an installer finds no planted DLL beside it.
3. VERIFY THE HASH BEFORE ANYTHING RUNS: a sha256 other than the catalogue's is refused, the file deleted.
4. INSTALL into ``<tool root>/<tool>/<version>/``. Inno Setup: ``/DIR=<target> /LOG=<staging>/install.log /SP-
   /NORESTART /CURRENTUSER`` (per-user, so no administrator is asked where the installer allows it). MSI:
   ``msiexec /i`` with the directory property, a verbose log and a per-user install. Zip: extracted in place,
   every member confined to the target. Source build: the deploy step installed it there already; it is
   verified and recorded here.
5. VERSION FILE: where the tool's probe reads one (:data:`~lab_commons.compute.toolchecks.VERSION_FILES`) and the
   install ships none, it is written beside the binary -- same name, same ``KEY=VALUE`` form.
6. PROBE: the lab-commons probe (:func:`~lab_commons.compute.presence.probe`) on the new binary, required at
   exactly this release. A refusal discards the new directory.
7. RECORD, then remove the previous release's directory. Only now does the probe find the new install.

THE INSTALLER'S OWN PROMPTS ARE NEVER SUPPRESSED. The default mode, ``silent``, is Inno's ``/SILENT`` (MSI's
``/passive``): progress is shown and no wizard question is asked, but any message the installer raises still
reaches the person at the screen. ``interactive`` runs the full wizard with the directory preset. There is no
very-silent mode: ``/VERYSILENT /SUPPRESSMSGBOXES`` would hide what the installer has to say from the person
whose machine it changes (2026-11-25: refused as undermining oversight).
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from lab_commons import launch
from lab_commons.compute.presence import Presence, Requirement, probe
from lab_commons.compute.toolchecks import CHECKS, VERSION_FILES, Check
from lab_commons.tools import managed
from lab_commons.tools.catalogue import CATALOGUE, Release

__all__ = [
    'DOWNLOAD_TIMEOUT',
    'MODES',
    'ToolReport',
    'command',
    'download',
    'install',
    'launch_installer',
    'sha256_of',
    'verify',
]

#: How the installer is shown: ``silent`` -- progress, no questions (the default); ``interactive`` -- the wizard.
MODES: Final = ('silent', 'interactive')

#: Wall-clock ceiling of one download, in seconds.
DOWNLOAD_TIMEOUT: Final = 600.0

#: The staged file's name per kind: an installer must keep its extension to run.
_PAYLOAD: Final = {'inno': 'installer.exe', 'msi': 'installer.msi', 'zip': 'archive.zip'}

#: Inno Setup's documented exit codes.
_INNO_EXITS: Final = {
    1: 'Setup failed to initialize',
    2: 'cancelled before installing',
    3: 'a fatal error preparing to install',
    4: 'a fatal error during installation',
    5: 'cancelled during installation',
    6: 'Setup was terminated',
    7: 'the Preparing to Install step refused to proceed',
    8: 'the Preparing to Install step needs a restart first',
}

#: msiexec's exit codes that are a success, and the common failures.
_MSI_SUCCESS: Final = frozenset({0, 3010})
_MSI_EXITS: Final = {
    1602: 'cancelled',
    1603: 'a fatal error during installation',
    1618: 'another installation is in progress',
    1925: 'insufficient privileges',
    3010: 'installed; a restart completes it',
}


@dataclass(frozen=True)
class ToolReport:
    """What the door did: ``installed``, ``skipped`` (already there, verified) or ``refused``, and why."""

    state: str
    message: str


def download(url: str, dest: Path) -> None:
    """Stream *url* (https only) to *dest*.

    Raises:
        ValueError: *url* is not https.
        OSError: the download failed.

    """
    if not url.startswith('https://'):
        msg = f'refusing a non-https URL: {url}'
        raise ValueError(msg)
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response, dest.open('wb') as out:  # noqa: S310
        shutil.copyfileobj(response, out)


def launch_installer(argv: list[str]) -> int:
    """Run an installer through the process door and wait: its exit code. Its own window shows."""
    return launch.run(argv, check=False).returncode


def sha256_of(path: Path) -> str:
    """The file's SHA-256, lower-case hex."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def command(release: Release, payload: Path, target: Path, log: Path, mode: str) -> list[str]:
    """The installer command for *release*: per-user, logged, no restart; ``silent`` shows progress, asks nothing."""
    if mode not in MODES:
        msg = f'mode {mode!r}; the modes are {list(MODES)}'
        raise ValueError(msg)
    if release.kind == 'inno':
        quiet = ['/SILENT'] if mode == 'silent' else []
        return [str(payload), f'/DIR={target}', f'/LOG={log}', '/SP-', '/NORESTART', '/CURRENTUSER', *quiet]
    if release.kind == 'msi':
        quiet = ['/passive'] if mode == 'silent' else []
        prop = release.dir_property or 'INSTALLDIR'
        per_user = ['ALLUSERS=2', 'MSIINSTALLPERUSER=1']
        return ['msiexec', '/i', str(payload), f'{prop}={target}', *per_user, '/norestart', '/l*v', str(log), *quiet]
    msg = f'a {release.kind} release runs no installer'
    raise ValueError(msg)


def verify(release: Release, binary: Path, *, checks: Mapping[str, Check] = CHECKS, now: datetime) -> Presence:
    """The lab-commons probe on *binary*, required at exactly *release*'s version."""
    found = replace(checks[release.tool], find=lambda: ('managed', binary))
    return probe(
        release.tool, found, Requirement(release.version, 'the release this door installs', exact=True), now=now
    )


def _evidence(presence: Presence) -> str:
    return '; '.join(f'{one.source}: {one.value}' for one in presence.evidence)


def _write_version_file(release: Release, binary: Path) -> None:
    """The tool's version file beside *binary*, where the probe reads one and the install ships none."""
    if release.tool not in VERSION_FILES:
        return
    name, key = VERSION_FILES[release.tool]
    file = binary.parent / name
    if file.exists():
        return
    file.write_text(
        f'# {release.tool} {release.version}: written by the install door (lab_commons.tools) -- the install shipped '
        f'no {name}\n{key}={release.version}\n',
        encoding='utf-8',
    )


def _extract(archive: Path, target: Path) -> None:
    """Extract *archive* into *target*, every member confined to it.

    Raises:
        ValueError: a member would land outside *target*.

    """
    with zipfile.ZipFile(archive) as zipped:
        root = target.resolve()
        for member in zipped.namelist():
            if not (root / member).resolve().is_relative_to(root):
                msg = f'{archive.name} member {member!r} would land outside {target}'
                raise ValueError(msg)
        zipped.extractall(target)  # every member checked above


class _Refused(Exception):
    """One step refused; the message is the outcome's."""


def install(
    release: Release,
    *,
    mode: str = 'silent',
    installer: Path | None = None,
    fetch: Callable[[str, Path], None] | None = None,
    run: Callable[[list[str]], int] | None = None,
    checks: Mapping[str, Check] = CHECKS,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ToolReport:
    """Install *release* under the managed root (the module docstring's steps); *installer*: a local copy to use.

    *fetch* and *run* default to :func:`download` and :func:`launch_installer`, read when called.
    """
    tool, version = release.tool, release.version
    try:
        before = managed.installed(tool)
    except ValueError as unread:
        before, note = None, f' (the old record was unreadable and is rewritten: {unread})'
    else:
        note = ''
    if before is not None and before.version == version:
        reading = verify(release, before.binary, checks=checks, now=now())
        if reading.present:
            return ToolReport('skipped', f'{tool} {version} is already installed at {before.binary} and verifies')
        note = f' (the recorded {tool} {version} failed its probe -- {_evidence(reading)} -- and is reinstalled)'
    try:
        if release.kind == 'source-build':
            binary = _built(release)
            source, sha256 = f'built by {release.builder}', None
        else:
            binary, source, sha256 = _installed(
                release, before=before, mode=mode, installer=installer, fetch=fetch, run=run
            )
        _probed(release, binary, before, verify(release, binary, checks=checks, now=now()))
    except _Refused as refused:
        return ToolReport('refused', str(refused) + note)
    managed.record(managed.Installed(tool, version, release.binary, source, sha256, now().isoformat()))
    if before is not None and before.version != version:
        shutil.rmtree(before.root, ignore_errors=True)
    replaced = f'; {tool} {before.version} removed' if before is not None and before.version != version else ''
    return ToolReport('installed', f'{tool} {version} installed at {binary}, verified and recorded{replaced}{note}')


def _probed(release: Release, binary: Path, before: managed.Installed | None, reading: Presence) -> None:
    """Refuse a new install its probe reads absent, discarding it -- never a deploy step's own build."""
    if reading.present:
        return
    if release.kind != 'source-build':
        _discard(release, before)
    msg = (
        f'the probe refused the new {release.tool} {release.version} at {binary}: {_evidence(reading)}; {_kept(before)}'
    )
    raise _Refused(msg)


def _kept(before: managed.Installed | None) -> str:
    if before is None:
        return 'nothing was installed before'
    return f'{before.tool} {before.version} at {before.root} is kept'


def _discard(release: Release, before: managed.Installed | None) -> None:
    """Remove a failed attempt's directory -- unless it is the recorded install being repaired."""
    if before is None or before.version != release.version:
        shutil.rmtree(managed.install_dir(release.tool, release.version), ignore_errors=True)


def _built(release: Release) -> Path:
    """A source build's binary, installed by its deploy step; refused naming the step when it is not there."""
    target = managed.install_dir(release.tool, release.version)
    binary = target / release.binary
    if not binary.is_file():
        msg = (
            f'{release.tool} {release.version} on {release.system} is built from source by {release.builder}, '
            f'which installs it into {target} (binary {release.binary}); run that step, then this door verifies '
            'and records it'
        )
        raise _Refused(msg)
    _write_version_file(release, binary)
    return binary


def _installed(
    release: Release,
    *,
    before: managed.Installed | None,
    mode: str,
    installer: Path | None,
    fetch: Callable[[str, Path], None] | None,
    run: Callable[[list[str]], int] | None,
) -> tuple[Path, str, str]:
    """Stage, hash-verify and install a downloaded release: its binary, its source and its sha256."""
    tool, version = release.tool, release.version
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    staged = managed.staging() / f'{tool}-{version}-{stamp}'
    staged.mkdir(parents=True)
    payload = staged / _PAYLOAD[release.kind]
    source = str(installer) if installer is not None else str(release.url)
    try:
        if installer is not None:
            shutil.copyfile(installer, payload)
        else:
            (fetch or download)(str(release.url), payload)
    except (OSError, ValueError) as failed:
        shutil.rmtree(staged, ignore_errors=True)
        msg = f'could not fetch {source}: {failed}; nothing ran -- re-run, or pass --installer <a local copy>'
        raise _Refused(msg) from None
    got = sha256_of(payload)
    if got != release.sha256:
        shutil.rmtree(staged, ignore_errors=True)
        msg = (
            f'sha256 mismatch for {source}: got {got}, {CATALOGUE.name} declares {release.sha256}; the file is '
            f'deleted and nothing ran -- re-fetch it, and if the vendor republished the release, measure the new '
            f'file against its published hash and update the row in {CATALOGUE}'
        )
        raise _Refused(msg)
    target = managed.install_dir(tool, version)
    if target.exists() and (before is None or before.version != version):
        shutil.rmtree(target)  # a failed attempt never recorded: nothing depends on it
    log = staged / 'install.log'
    if release.kind == 'zip':
        try:
            _extract(payload, target)
        except (ValueError, zipfile.BadZipFile) as bad:
            _discard(release, before)
            msg = f'{bad}; {_kept(before)}'
            raise _Refused(msg) from None
    else:
        code = (run or launch_installer)(command(release, payload, target, log, mode))
        if code not in (_MSI_SUCCESS if release.kind == 'msi' else {0}):
            meaning = (_MSI_EXITS if release.kind == 'msi' else _INNO_EXITS).get(code, 'see its log')
            payload.unlink(missing_ok=True)
            _discard(release, before)
            msg = f'the {release.kind} installer exited {code} ({meaning}); its log: {log}; {_kept(before)}'
            raise _Refused(msg)
    shutil.rmtree(staged, ignore_errors=True)
    binary = target / release.binary
    if not binary.is_file():
        _discard(release, before)
        msg = f'the install put no {release.binary} in {target} (the catalogue row is wrong?); {_kept(before)}'
        raise _Refused(msg)
    _write_version_file(release, binary)
    return binary, source, got
