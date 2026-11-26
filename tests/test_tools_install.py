"""``python -m lab_commons.tools install``: a vendor tool goes under the managed root through ONE door.

Every install here runs a FAKE installer (the ``run`` seam) and a fake download (the ``fetch`` seam) inside the
test's own machine home (``MOTRONICS_HOME``); no vendor installer ever runs. Verification is the REAL lab-commons
probe (:data:`~lab_commons.compute.toolchecks.CHECKS`), so a fake install must carry what a real one does.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lab_commons.compute import toolchecks
from lab_commons.compute.presence import Requirement
from lab_commons.compute.toolchecks import FEMM_VERSION_FILE
from lab_commons.tools import install as door
from lab_commons.tools import managed
from lab_commons.tools.__main__ import main
from lab_commons.tools.catalogue import CATALOGUE, Release, choose, compose, load, requirements

NEW, OLD = '5.0.20261112', '5.0.20261021'
PAYLOAD = b'MZ a fake installer'
BINARY = 'bin/femm_headlessw.exe' if sys.platform == 'win32' else 'bin/femm_headless'


@pytest.fixture(autouse=True)
def machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Give each test its own machine home; a Windows binary's file version is the one its fake installer stamped."""
    monkeypatch.setenv('MOTRONICS_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(toolchecks, 'file_version', lambda path: _STAMPS.get(Path(path)))
    _STAMPS.clear()
    return tmp_path / 'home'


#: The Windows file version each fake installer stamped on the binary it wrote (femm-qt's 4-field form).
_STAMPS: dict[Path, str] = {}


def _four_field(version: str) -> str:
    major, minor, patch = version.split('.')
    return f'{major}.{minor}.{patch[:4]}.{int(patch[4:])}'


def _release(version: str = NEW, *, sha256: str | None = None, kind: str = 'inno') -> Release:
    return Release(
        tool='femm',
        version=version,
        system='windows',
        kind=kind,
        binary=BINARY,
        url='https://example.invalid/femm.exe',
        sha256=sha256 or hashlib.sha256(PAYLOAD).hexdigest(),
    )


class FakeInstaller:
    """Records every command; writes the binary under ``/DIR=`` and exits *code* -- what an Inno installer does."""

    def __init__(self, code: int = 0, *, stamp: str | None = None) -> None:
        """*stamp*: the version the installed binary states (default: the release asked for)."""
        self.code, self.stamp, self.commands = code, stamp, []

    def __call__(self, argv: list[str]) -> int:
        self.commands.append(argv)
        target = Path(next(a for a in argv if a.startswith('/DIR=')).removeprefix('/DIR='))
        exe = target / BINARY
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b'MZ fake femm')
        exe.chmod(0o755)
        _STAMPS[exe] = _four_field(self.stamp or target.name)  # Windows reads this; Linux the door's version file
        return self.code


def _fetch(payload: bytes = PAYLOAD) -> Callable[[str, Path], None]:
    def fetch(_url: str, dest: Path) -> None:
        dest.write_bytes(payload)

    return fetch


def _install(release: Release, run: FakeInstaller, payload: bytes = PAYLOAD) -> door.Outcome:
    return door.install(release, mode='silent', fetch=_fetch(payload), run=run)


def test_a_release_installs_into_its_versioned_directory_verified_and_recorded(machine: Path) -> None:
    """PLANTED CONTROL: the whole path passes, so each refusal below is the door saying no, not a broken fake."""
    run = FakeInstaller()
    outcome = _install(_release(), run)
    assert outcome.state == 'installed', outcome.message
    target = machine / 'tools' / 'femm' / NEW
    assert (target / BINARY).is_file()
    assert (target / BINARY).parent.joinpath(FEMM_VERSION_FILE).read_text(encoding='utf-8').splitlines()[-1] == (
        f'FEMM_VERSION={NEW}'
    )
    one = managed.installed('femm')
    assert one is not None
    assert (one.version, one.binary) == (NEW, target / BINARY)
    assert not any((machine / 'tools' / managed.STAGING).iterdir()), 'a verified install leaves no staging behind'


def test_the_inno_command_shows_progress_asks_nothing_and_never_suppresses_prompts() -> None:
    run = FakeInstaller()
    _install(_release(), run)
    (argv,) = run.commands
    assert '/SILENT' in argv
    assert '/CURRENTUSER' in argv
    assert {'/SP-', '/NORESTART'} <= set(argv)
    assert any(a.startswith('/LOG=') for a in argv)
    assert not {'/VERYSILENT', '/SUPPRESSMSGBOXES'} & set(argv), 'the installer keeps its own prompts'
    interactive = FakeInstaller()
    door.install(_release(OLD), mode='interactive', fetch=_fetch(), run=interactive)
    assert '/SILENT' not in interactive.commands[0], 'interactive shows the wizard, its directory preset'


def test_a_wrong_sha256_is_refused_deleted_and_nothing_runs(machine: Path) -> None:
    run = FakeInstaller()
    outcome = _install(_release(sha256='0' * 64), run)
    assert outcome.state == 'refused'
    assert run.commands == [], 'nothing ran'
    assert 'sha256' in outcome.message
    assert 'tools.toml' in outcome.message, 'the refusal names where the declared hash lives'
    assert not list((machine / 'tools').rglob('installer.exe')), 'the mismatched file is deleted'
    assert managed.installed('femm') is None


def test_an_installed_exact_version_is_skipped_by_name() -> None:
    first = FakeInstaller()
    assert _install(_release(), first).state == 'installed'
    again = FakeInstaller()
    outcome = _install(_release(), again)
    assert outcome.state == 'skipped'
    assert again.commands == []
    assert f'femm {NEW}' in outcome.message
    assert 'already installed' in outcome.message


def test_a_failed_install_keeps_the_old_version(machine: Path) -> None:
    assert _install(_release(OLD), FakeInstaller()).state == 'installed'
    failing = FakeInstaller(code=4)
    outcome = _install(_release(NEW), failing)
    assert outcome.state == 'refused'
    assert 'exited 4' in outcome.message
    one = managed.installed('femm')
    assert one is not None
    assert one.version == OLD
    assert one.binary.is_file(), 'the old install is untouched'
    assert not (machine / 'tools' / 'femm' / NEW).exists(), 'the failed attempt is removed'


@pytest.mark.windows  # on Linux the version is the version file the door writes; the stamp is a Windows reading
def test_an_install_the_probe_refuses_keeps_the_old_version(machine: Path) -> None:
    """The installer exits 0 but its binary states another release: the probe, not the exit code, decides."""
    assert _install(_release(OLD), FakeInstaller()).state == 'installed'
    outcome = _install(_release(NEW), FakeInstaller(stamp='5.0.20260101'))
    assert outcome.state == 'refused'
    assert 'probe' in outcome.message
    assert managed.installed('femm').version == OLD  # type: ignore[union-attr]
    assert not (machine / 'tools' / 'femm' / NEW).exists()


def test_a_verified_new_version_replaces_the_old_one(machine: Path) -> None:
    assert _install(_release(OLD), FakeInstaller()).state == 'installed'
    assert _install(_release(NEW), FakeInstaller()).state == 'installed'
    assert managed.installed('femm').version == NEW  # type: ignore[union-attr]
    assert not (machine / 'tools' / 'femm' / OLD).exists(), 'the old version goes once the new one verifies'


def test_a_local_installer_copy_is_verified_like_a_download(tmp_path: Path) -> None:
    copy = tmp_path / 'femm-setup.exe'
    copy.write_bytes(b'tampered')
    run = FakeInstaller()
    outcome = door.install(_release(), mode='silent', installer=copy, run=run, fetch=_fetch())
    assert outcome.state == 'refused'
    assert run.commands == []
    assert copy.is_file(), 'the caller own copy is an input: never deleted'


def test_a_source_build_waits_for_its_deploy_step_then_is_verified_and_recorded(machine: Path) -> None:
    built = Release(
        tool='femm', version=NEW, system='linux', kind='source-build', binary='bin/femm_headless', builder='the step'
    )
    outcome = door.install(built, mode='silent', fetch=_fetch(), run=FakeInstaller())
    assert outcome.state == 'refused'
    assert 'the step' in outcome.message
    assert str(machine / 'tools' / 'femm' / NEW) in outcome.message, 'the refusal names where the step installs'
    exe = machine / 'tools' / 'femm' / NEW / 'bin' / 'femm_headless'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'ELF')
    exe.chmod(0o755)
    _STAMPS[exe] = _four_field(NEW)
    (exe.parent / FEMM_VERSION_FILE).write_text(f'# femm-qt build stamp\nFEMM_VERSION={NEW}\n', encoding='utf-8')
    outcome = door.install(built, mode='silent', fetch=_fetch(), run=FakeInstaller())
    assert outcome.state == 'installed', outcome.message
    assert managed.installed('femm').version == NEW  # type: ignore[union-attr]


def test_the_requirement_chooses_the_release() -> None:
    table = load(_CATALOGUE)
    exact = Requirement(NEW, 'only the newest', exact=True)
    assert choose(table, 'femm', 'windows', exact).version == NEW
    assert choose(table, 'femm', 'windows', Requirement('5.0', 'FEMM 5')).version == NEW, 'newest meeting a floor'
    assert choose(table, 'femm', 'windows', None, version=OLD).version == OLD
    with pytest.raises(LookupError, match=r'==5\.0\.20261112'):
        choose(table, 'femm', 'windows', exact, version=OLD)
    with pytest.raises(LookupError, match='macos'):
        choose(table, 'femm', 'macos', None)
    with pytest.raises(LookupError, match='femm'):
        choose(table, 'nosuch', 'windows', None)


_CATALOGUE = f"""
[femm]
mode = "managed"

[femm.releases."{NEW}".windows]
kind = "inno"
url = "https://example.invalid/new.exe"
sha256 = "{'a' * 64}"
binary = "bin/femm_headlessw.exe"

[femm.releases."{OLD}".windows]
kind = "inno"
url = "https://example.invalid/old.exe"
sha256 = "{'b' * 64}"
binary = "bin/femm_headlessw.exe"
"""


@pytest.mark.parametrize(
    ('row', 'refusal'),
    [
        ('kind = "exe"\nbinary = "b"', 'kind'),
        ('kind = "inno"\nbinary = "b"\nsha256 = "' + 'a' * 64 + '"', 'url'),
        ('kind = "inno"\nbinary = "b"\nurl = "http://x/y.exe"\nsha256 = "' + 'a' * 64 + '"', 'https'),
        ('kind = "inno"\nbinary = "b"\nurl = "https://x/y.exe"\nsha256 = "short"', 'sha256'),
        ('kind = "inno"\nbinary = "../b"\nurl = "https://x/y.exe"\nsha256 = "' + 'a' * 64 + '"', 'binary'),
        ('kind = "source-build"\nbinary = "b"', 'builder'),
        ('kind = "zip"\nbinary = "b"\nurl = "https://x/y.zip"\nsha256 = "' + 'a' * 64 + '"\nextra = 1', 'extra'),
    ],
)
def test_a_malformed_release_row_is_refused_by_name(row: str, refusal: str) -> None:
    with pytest.raises(ValueError, match=refusal):
        load(f'[femm]\nmode = "managed"\n[femm.releases."{NEW}".windows]\n{row}\n')


def test_the_family_catalogue_loads_and_names_the_measured_femm_release() -> None:
    table = load(CATALOGUE.read_text(encoding='utf-8'))
    windows = table['femm'].releases[NEW]['windows']
    assert windows.kind == 'inno'
    assert windows.sha256 == '540ba8e45fd56dba61be696a43fd1a80989969d50562a9a46f9aab9b152085d4'
    assert Path(windows.binary).name == toolchecks.FEMM_HEADLESS['windows'], 'one binary name, two readers'
    assert Path(table['femm'].releases[NEW]['linux'].binary).name == toolchecks.FEMM_HEADLESS['linux']
    assert set(table) <= set(toolchecks.CHECKS), 'a catalogued tool is verified by its probe'
    users = {name for name, tool in table.items() if tool.mode == 'user-installed'}
    assert users == {'jmag', 'matlab', 'aedt', 'fluent', 'mapdl', 'lsdyna'}
    assert all(toolchecks.CHECKS[name].within for name in users), 'a user-installed tool is probed under its home'
    assert {table[name].vendor for name in ('aedt', 'fluent', 'mapdl', 'lsdyna')} == {'ansys'}


@pytest.mark.parametrize(
    ('text', 'refusal'),
    [
        ('[femm]\nmode = "bundled"', 'mode'),
        ('[plecs]\nmode = "user-installed"', 'no lab-commons probe for plecs'),
        ('[jmag]\nmode = "user-installed"\n[jmag.releases."25.2".windows]\nkind = "zip"', 'no releases'),
        ('[femm]\nmode = "managed"', 'states its releases'),
        ('[femm]\nmode = "managed"\nvendor = "x"\n[femm.releases."5.0".windows]\nkind = "zip"', 'vendor'),
        ('[jmag]\nmode = "user-installed"\nseats = 3', 'seats'),
    ],
)
def test_a_malformed_tool_is_refused_by_name(text: str, refusal: str) -> None:
    with pytest.raises(ValueError, match=refusal):
        load(text)


def test_a_user_installed_tool_has_no_release_to_download() -> None:
    with pytest.raises(LookupError, match='user-installed'):
        choose(load('[jmag]\nmode = "user-installed"'), 'jmag', 'windows', None)


def test_the_cli_installs_lists_and_checks(machine: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(door, 'download', _fetch())
    monkeypatch.setattr(door, 'launch_installer', FakeInstaller())
    catalogue = tmp_path / 'tools.toml'
    rows = _CATALOGUE.replace('a' * 64, hashlib.sha256(PAYLOAD).hexdigest()).replace('bin/femm_headlessw.exe', BINARY)
    catalogue.write_text(rows, encoding='utf-8')
    monkeypatch.setattr('lab_commons.tools.catalogue.CATALOGUE', catalogue)
    monkeypatch.setattr('lab_commons.tools.__main__.os_name', lambda: 'windows')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'pyproject.toml').write_text(
        f'[tool.lab_commons.tools]\nfemm = {{exact = "{NEW}", reason = "only the newest"}}\n', encoding='utf-8'
    )
    assert main(['install', 'femm', '--repo', str(repo)]) == 0
    assert managed.installed('femm').version == NEW  # type: ignore[union-attr]
    assert main(['install', 'femm', '--repo', str(repo)]) == 0, 'skipped is a success'
    assert main(['list', '--json']) == 0
    assert main(['check', 'femm', '--repo', str(repo)]) == 0
    record = json.loads((machine / 'tools' / 'femm' / managed.RECORD).read_text(encoding='utf-8'))
    assert record['version'] == NEW


# --- the family floor (user ruling 2026-11-26: FEMM >= 5.0.20261112, the newest installed) ----------------------

NEWER = '5.0.20270301'

_FLOORED = f"""
[femm]
mode = "managed"
floor = "{NEW}"
reason = "only FEMM 5 at {NEW} or newer"

[femm.releases."{NEW}".windows]
kind = "inno"
url = "https://example.invalid/new.exe"
sha256 = "{'a' * 64}"
binary = "bin/femm_headlessw.exe"

[femm.releases."{NEWER}".windows]
kind = "inno"
url = "https://example.invalid/newer.exe"
sha256 = "{'c' * 64}"
binary = "bin/femm_headlessw.exe"
"""


def test_the_family_catalogue_states_the_femm_floor_and_no_release_below_it() -> None:
    floor = load(CATALOGUE.read_text(encoding='utf-8'))['femm'].floor
    assert floor is not None
    assert (floor.floor, floor.exact) == (NEW, False), 'a FLOOR, never one pinned release'
    assert floor.refusal(OLD) is not None, 'planted control: an older FEMM is refused'
    assert floor.refusal(NEWER) is None, 'planted control: a newer FEMM is accepted'


def test_the_floor_refuses_an_older_reading_by_name_and_admits_a_newer_one() -> None:
    tool = load(_FLOORED)['femm']
    required = requirements(None, {'femm': tool})['femm']
    assert required.refusal(OLD) == f'below the floor {NEW} (only FEMM 5 at {NEW} or newer)'
    assert required.refusal(NEW) is None
    assert required.refusal(NEWER) is None


def test_the_door_installs_the_newest_release_at_or_above_the_floor() -> None:
    table = load(_FLOORED)
    required = requirements(None, table)['femm']
    assert choose(table, 'femm', 'windows', required).version == NEWER
    with pytest.raises(LookupError, match='not in'):
        choose(table, 'femm', 'windows', required, version=OLD)


def test_a_release_row_below_the_floor_is_refused_by_name() -> None:
    old_row = _CATALOGUE.replace('mode = "managed"', f'mode = "managed"\nfloor = "{NEW}"\nreason = "r"')
    with pytest.raises(ValueError, match=rf"\['{OLD}'\] are below the floor {NEW}"):
        load(old_row)


@pytest.mark.parametrize(
    ('extra', 'refusal'),
    [('floor = "5.0"', 'floor and reason come together'), ('floor = "new"\nreason = "r"', 'must be a version')],
)
def test_a_malformed_floor_is_refused_by_name(extra: str, refusal: str) -> None:
    with pytest.raises(ValueError, match=refusal):
        load(
            f'[femm]\nmode = "managed"\n{extra}\n[femm.releases."{NEW}".windows]\nkind = "zip"\nbinary = "b"\n'
            f'url = "https://x/y.zip"\nsha256 = "{"a" * 64}"\n'
        )


def test_a_repo_may_narrow_the_family_floor_never_lower_it(tmp_path: Path) -> None:
    tool = load(_FLOORED)['femm']
    assert compose(tool, None) == tool.floor
    assert compose(tool, Requirement('5.0', 'FEMM 5')) == tool.floor, 'a lower repo floor yields to the family'
    assert compose(tool, Requirement(NEWER, 'needs the newer')).floor == NEWER, 'a higher repo floor narrows'
    assert compose(tool, Requirement(NEWER, 'one', exact=True)).exact
    with pytest.raises(ValueError, match=rf'{OLD} is below the floor {NEW}'):
        compose(tool, Requirement(OLD, 'pinned old', exact=True))
    with pytest.raises(ValueError, match='admits nothing'):
        compose(tool, Requirement('5.0', 'FEMM 5', below=NEW, incompatible='x'))
    (tmp_path / 'pyproject.toml').write_text('[tool.lab_commons.tools]\nfemm = {floor = "5.0", reason = "r"}\n')
    assert requirements(tmp_path, {'femm': tool})['femm'] == tool.floor
