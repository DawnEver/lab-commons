"""FEMM is found under the MANAGED tool root first; a hand install elsewhere reads unmanaged, naming the door.

User ruling 2026-11-25: installing a vendor tool is a family mechanism (``python -m lab_commons.tools install``),
so a FEMM outside ``<home>/tools/femm/<version>/`` -- the old ``~/.local/opt/femm-qt``, ``C:/femm-qt``, one on
``PATH`` -- is never silently accepted. ``FEMM_DIR`` stays what it is: an explicit declaration a person or a
job file makes, read when nothing is managed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from lab_commons.compute import toolchecks
from lab_commons.compute.presence import probe
from lab_commons.compute.toolchecks import CHECKS, Unmanaged, femm_find
from lab_commons.tools import managed

NOW: Final = datetime(2026, 11, 25, 12, tzinfo=UTC)
FEMM_BINARIES: Final = [('linux', 'femm_headless'), ('win32', 'femm_headlessw.exe')]


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give each test its own machine home and user home, so no install on the host running it is ever read."""
    monkeypatch.setenv('MOTRONICS_HOME', str(tmp_path / 'home'))
    for name in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(name, str(tmp_path / 'user'))
    legacy = dict.fromkeys(toolchecks.FEMM_LEGACY_HOMES, ('~/.local/opt/femm-qt/bin',))
    monkeypatch.setattr(toolchecks, 'FEMM_LEGACY_HOMES', legacy)
    return tmp_path


def _legacy(machine: Path, binary: str) -> Path:
    exe = machine / 'user' / '.local' / 'opt' / 'femm-qt' / 'bin' / binary
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    return exe


def _managed(machine: Path, binary: str, version: str = '5.0.20261112') -> Path:
    exe = machine / 'home' / 'tools' / 'femm' / version / 'bin' / binary
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    managed.record(managed.Installed('femm', version, f'bin/{binary}', source='test', sha256=None, at=NOW.isoformat()))
    return exe


@pytest.mark.parametrize(('platform', 'binary'), FEMM_BINARIES)
def test_the_managed_install_is_found_before_femm_dir_legacy_and_path(
    machine: Path, platform: str, binary: str
) -> None:
    _legacy(machine, binary)
    declared = machine / 'dev' / 'bin' / binary
    declared.parent.mkdir(parents=True)
    declared.write_bytes(b'')
    exe = _managed(machine, binary)
    env = {'FEMM_DIR': str(machine / 'dev')}
    assert femm_find(platform=platform, environ=env, which=lambda b: f'/p/{b}') == ('managed', exe)


@pytest.mark.parametrize(('platform', 'binary'), FEMM_BINARIES)
def test_a_record_whose_binary_is_gone_is_not_a_managed_install(machine: Path, platform: str, binary: str) -> None:
    """PLANTED CONTROL: the managed leg reads the binary, not merely the record."""
    _managed(machine, binary).unlink()
    assert femm_find(platform=platform, environ={}, which=lambda _b: None) is None


@pytest.mark.parametrize(('platform', 'binary'), FEMM_BINARIES)
def test_femm_dir_is_read_when_nothing_is_managed(machine: Path, platform: str, binary: str) -> None:
    env, nowhere = {'FEMM_DIR': str(machine / 'dev')}, lambda _b: None
    assert femm_find(platform=platform, environ=env, which=nowhere) is None
    (machine / 'dev' / 'bin').mkdir(parents=True)
    (machine / 'dev' / 'bin' / binary).write_bytes(b'')
    assert femm_find(platform=platform, environ=env, which=nowhere) == ('FEMM_DIR', machine / 'dev' / 'bin' / binary)
    assert femm_find(platform='darwin', environ=env, which=nowhere) is None, 'no FEMM 5 build is known there'


@pytest.mark.parametrize(('platform', 'binary'), FEMM_BINARIES)
def test_a_legacy_only_install_reads_unmanaged_naming_the_door(machine: Path, platform: str, binary: str) -> None:
    exe = _legacy(machine, binary)
    with pytest.raises(Unmanaged, match=r'unmanaged.*python -m lab_commons\.tools install femm') as refused:
        femm_find(platform=platform, environ={}, which=lambda _b: None)
    assert str(exe) in str(refused.value)
    exe.unlink()
    with pytest.raises(Unmanaged, match='PATH'):
        femm_find(platform=platform, environ={}, which=lambda b: f'/p/{b}')


@pytest.mark.parametrize(('platform', 'binary'), FEMM_BINARIES)
def test_the_probe_records_an_unmanaged_install_as_absent(machine: Path, platform: str, binary: str) -> None:
    _legacy(machine, binary)
    femm = replace(CHECKS['femm'], find=lambda: femm_find(platform=platform, environ={}, which=lambda _b: None))
    one = probe('femm', femm, None, now=NOW)
    assert not one.present
    assert one.evidence[-1].source == 'find'
    assert one.evidence[-1].value.startswith('unmanaged'), one.evidence


def test_each_os_names_its_legacy_homes() -> None:
    assert set(toolchecks.FEMM_LEGACY_HOMES) == set(toolchecks.FEMM_HEADLESS)
    assert '~/.local/opt/femm-qt/bin' in toolchecks.FEMM_LEGACY_HOMES['linux']
    assert 'C:/femm-qt/bin' in toolchecks.FEMM_LEGACY_HOMES['windows']
