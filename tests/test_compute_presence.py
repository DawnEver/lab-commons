"""D21: a vendor tool is present only when found, at the declared version, smoke-tested, and not failed since.

Every OS reading is a fake :class:`Check`; the cache lives in the test's own machine directory.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

from lab_commons.compute.presence import (
    CHECKS,
    TTL,
    Check,
    cached,
    declared_versions,
    demote,
    femm_find,
    matlab_find,
    matlab_version,
    probe,
    refresh,
    simba_find,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
EXE = Path('/opt/tool/bin/tool')


class Fake:
    """A tool whose three readings a test sets, counting how often it is smoke-tested."""

    def __init__(self, *, found: bool = True, version: str | None = '5.0', smoke: bool = True) -> None:
        """Found, at version 5.0, passing its smoke -- unless told otherwise."""
        self.found, self.version, self.passes, self.smokes = found, version, smoke, 0

    def check(self) -> Check:
        def smoke(_path: Path) -> tuple[bool, str]:
            self.smokes += 1
            return self.passes, 'exit 0' if self.passes else 'exit 1'

        return Check(
            lambda: ('install dir', EXE) if self.found else None,
            lambda _p: ('--version', self.version) if self.version else None,
            smoke,
        )


def test_found_at_the_declared_version_and_smoked_is_present_with_its_evidence() -> None:
    """PLANTED CONTROL for every refusal below: all four agree, and each reading is on record."""
    one = probe('femm', Fake().check(), '5.0', now=NOW)
    assert one.present
    assert [e.source for e in one.evidence] == ['find: install dir', 'version: --version', 'smoke']
    assert one.evidence[0].value == str(EXE)
    assert {e.time for e in one.evidence} == {NOW.isoformat()}


@pytest.mark.parametrize(
    ('fake', 'declared', 'last'),
    [
        (Fake(found=False), '5.0', 'not found'),
        (Fake(version=None), '5.0', 'unreadable'),
        (Fake(version='4.2'), '5.0', 'declared 5.0'),
        (Fake(), None, 'none declared in [tool.lab_commons.tools]'),
        (Fake(smoke=False), '5.0', 'FAILED: exit 1'),
    ],
)
def test_any_one_disagreeing_source_makes_it_absent_and_names_why(fake: Fake, declared: str | None, last: str) -> None:
    one = probe('femm', fake.check(), declared, now=NOW)
    assert not one.present
    assert one.evidence[-1].value == last


def test_a_failed_step_runs_no_later_step() -> None:
    fake = Fake(version='4.2')
    probe('femm', fake.check(), '5.0', now=NOW)
    assert fake.smokes == 0, 'a version mismatch never spends a licence on a smoke'


def test_a_reading_is_cached_for_its_ttl_and_retaken_after(tmp_path: Path) -> None:
    fake, state = Fake(), tmp_path / 'presence.json'
    assert refresh({'femm': '5.0'}, checks={'femm': fake.check()}, state=state, now=NOW)['femm'].present
    refresh({'femm': '5.0'}, checks={'femm': fake.check()}, state=state, now=NOW + TTL / 2)
    assert fake.smokes == 1, 'inside its TTL the smoke is not run again'
    later = NOW + TTL + timedelta(seconds=1)
    assert not cached(checks={'femm': fake.check()}, state=state, now=later)['femm'].present, 'stale reads absent'
    refresh({'femm': '5.0'}, checks={'femm': fake.check()}, state=state, now=later)
    assert fake.smokes == 2


def test_never_probed_is_absent(tmp_path: Path) -> None:
    one = cached(checks={'femm': Fake().check()}, state=tmp_path / 'none.json', now=NOW)['femm']
    assert not one.present
    assert one.evidence[-1].value == 'never run on this host'


def test_a_real_failure_demotes_until_the_next_smoke_passes(tmp_path: Path) -> None:
    fake, state = Fake(), tmp_path / 'presence.json'
    checks = {'femm': fake.check()}
    refresh({'femm': '5.0'}, checks=checks, state=state, now=NOW)
    demote(['femm'], 'exit 4 on tests/femm', state=state, now=NOW + timedelta(minutes=1))
    after = cached(checks=checks, state=state, now=NOW + timedelta(minutes=2))['femm']
    assert not after.present
    assert (after.evidence[-1].source, after.evidence[-1].value) == ('failure', 'exit 4 on tests/femm')
    fake.passes = False
    assert not refresh({'femm': '5.0'}, checks=checks, state=state, now=NOW + timedelta(minutes=3))['femm'].present
    assert fake.smokes == 2, 'a demoted tool is smoke-tested again at once, inside its TTL'
    fake.passes = True
    demote(['femm'], 'again', state=state, now=NOW + timedelta(minutes=4))
    assert refresh({'femm': '5.0'}, checks=checks, state=state, now=NOW + timedelta(minutes=5))['femm'].present


def test_the_family_version_is_declared_once_in_the_serving_repo(tmp_path: Path) -> None:
    assert declared_versions(tmp_path) == {}
    (tmp_path / 'pyproject.toml').write_text('[tool.lab_commons.tools]\nfemm = "5.0"\nmatlab = "R2024b"\n', 'utf-8')
    assert declared_versions(tmp_path) == {'femm': '5.0', 'matlab': 'R2024b'}
    (tmp_path / 'pyproject.toml').write_text('[tool.lab_commons.tools]\nfemm = 5\n', 'utf-8')
    with pytest.raises(ValueError, match='version string'):
        declared_versions(tmp_path)


def test_a_printed_version_names_the_declared_one_as_a_whole_token() -> None:
    reads = {'v20.11.0': ('20.11.0', '20.1'), 'FEMM 5.0 (build 7)': ('5.0', '5.01')}
    for printed, (same, other) in reads.items():
        assert probe('t', Fake(version=printed).check(), same, now=NOW).present
        assert not probe('t', Fake(version=printed).check(), other, now=NOW).present


@pytest.mark.parametrize('tool', ['femm', 'jmag', 'matlab', 'node', 'simba'])
def test_every_vendor_tool_has_a_check(tool: str) -> None:
    assert tool in CHECKS


@pytest.mark.parametrize(('platform', 'binary'), [('linux', 'femm_headless'), ('win32', 'femm_headlessw.exe')])
def test_femm_is_found_under_femm_dir_else_on_path(tmp_path: Path, platform: str, binary: str) -> None:
    env, nowhere = {'FEMM_DIR': str(tmp_path)}, lambda _b: None
    assert femm_find(platform=platform, environ=env, which=nowhere) is None
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / binary).write_bytes(b'')
    assert femm_find(platform=platform, environ=env, which=nowhere) == ('FEMM_DIR', tmp_path / 'bin' / binary)
    (tmp_path / 'bin' / binary).unlink()
    assert femm_find(platform=platform, environ=env, which=lambda b: f'/p/{b}') == ('PATH', Path(f'/p/{binary}'))
    assert femm_find(platform='darwin', environ=env, which=nowhere) is None, 'no FEMM 5 build is known there'


@pytest.mark.parametrize(
    ('platform', 'launcher'),
    [('win32', 'R2024b/bin/matlab.exe'), ('linux', 'R2024b/bin/matlab'), ('darwin', 'MATLAB_R2024b.app/bin/matlab')],
)
def test_matlab_is_found_in_its_install_and_its_release_is_its_version(
    tmp_path: Path, platform: str, launcher: str
) -> None:
    system = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}[platform]
    pattern = 'MATLAB_R*.app/bin/matlab' if system == 'macos' else launcher.replace('R2024b', 'R*')
    installs = {system: ((str(tmp_path), pattern),)}
    assert matlab_find(platform=platform, which=lambda _p: None, installs=installs) is None
    (tmp_path / launcher).parent.mkdir(parents=True)
    (tmp_path / launcher).write_bytes(b'')
    found = matlab_find(platform=platform, which=lambda _p: None, installs=installs)
    assert found == ('install dir', tmp_path / launcher)
    assert matlab_version(found[1]) == ('install path', 'R2024b')


def test_simba_is_found_where_its_python_api_imports() -> None:
    spec = ModuleSpec('aesim.simba', None, origin='/venv/aesim/simba/__init__.py')
    assert simba_find(find=lambda name: spec if name == 'aesim.simba' else None) == ('import', Path(spec.origin))
    namespace = ModuleSpec('aesim.simba', None)  # PLANTED: a namespace package has no origin file
    assert simba_find(find=lambda _name: namespace) == ('import', Path(sys.executable))
    assert simba_find(find=lambda _name: None) is None

    def missing_parent(_name: str) -> object:
        msg = 'aesim'
        raise ModuleNotFoundError(msg)

    assert simba_find(find=missing_parent) is None, 'no aesim package at all is absent, never an error'
