"""A USER-INSTALLED tool (JMAG, Ansys, MATLAB): the door reads the home this machine declares and probes it.

The machine file and the config source's host file are the test's own; the "install" is a fake executable whose
version the real probe reads (the Windows file-version reader is faked). Nothing is downloaded or run.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from lab_commons.compute import toolchecks
from lab_commons.compute.presence import Requirement
from lab_commons.tools import install as door
from lab_commons.tools import vendor
from lab_commons.tools.__main__ import main
from lab_commons.tools.catalogue import Tool

NOW: Final = datetime(2026, 11, 25, 12, tzinfo=UTC)
JMAG: Final = Tool('jmag', 'user-installed', 'jmag')
FLOOR: Final = Requirement('25.2', 'headless designer.exe -w file jobs')
DOOR: Final = 'with the vendor installer, then record it'


@pytest.fixture
def stamps(monkeypatch: pytest.MonkeyPatch) -> dict[Path, str]:
    """The Windows file version each fake designer carries."""
    found: dict[Path, str] = {}
    monkeypatch.setattr(toolchecks, 'file_version', lambda path: found.get(Path(path)))
    return found


def _designer(home: Path, stamps: dict[Path, str], version: str = '25.2.1.0') -> Path:
    exe = home / 'designer.exe'
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b'MZ fake designer')
    exe.chmod(0o755)
    stamps[exe] = version
    return exe


def _machine(tmp_path: Path, home: Path | None, *, vendor_key: str = 'jmag') -> dict[str, object]:
    """A rendered machine file naming host T, whose host file declares *home* for *vendor_key*."""
    source = tmp_path / 'hosts-config'
    (source / 'hosts').mkdir(parents=True, exist_ok=True)
    row = f'[dev.vendors.{vendor_key}]\nseats = 3\n' + (f'home = "{home.as_posix()}"\n' if home else '')
    (source / 'hosts' / 'T.toml').write_text(f'[dev]\nos = "windows"\n\n{row}', encoding='utf-8')
    return {'devhost': {'host': 'T', 'source': str(source)}}


def test_the_declared_home_is_read_from_the_host_file(tmp_path: Path) -> None:
    machine = _machine(tmp_path, tmp_path / 'JMAG-Designer25.2')
    declared = vendor.declared_home('jmag', machine=machine)
    assert declared.home == tmp_path / 'JMAG-Designer25.2'
    assert declared.where.endswith('T.toml [dev.vendors.jmag]')
    assert vendor.declared_home('ansys', machine=machine).home is None
    assert 'names no [devhost]' in vendor.declared_home('jmag', machine={}).where


def test_a_declared_home_whose_probe_passes_is_recorded(tmp_path: Path, stamps: dict[Path, str]) -> None:
    """PLANTED CONTROL: the whole path passes, so each refusal below is the door saying no."""
    home = tmp_path / 'JMAG-Designer25.2'
    exe = _designer(home, stamps)
    outcome = vendor.record(JMAG, FLOOR, now=NOW, machine=_machine(tmp_path, home))
    assert outcome.state == 'recorded', outcome.message
    assert str(exe) in outcome.message
    assert '25.2.1.0' in outcome.message


def test_a_version_below_the_requirement_is_refused_naming_the_vendor_installer(
    tmp_path: Path, stamps: dict[Path, str]
) -> None:
    home = tmp_path / 'JMAG-Designer25.1'
    _designer(home, stamps, version='25.1.0.0')
    outcome = vendor.record(JMAG, FLOOR, now=NOW, machine=_machine(tmp_path, home))
    assert outcome.state == 'refused'
    assert 'below the floor 25.2' in outcome.message
    assert f'install jmag 25.2 or newer {DOOR}' in outcome.message


def test_a_home_with_no_binary_is_refused(tmp_path: Path) -> None:
    home = tmp_path / 'JMAG-Designer25.2'
    home.mkdir()
    outcome = vendor.record(JMAG, FLOOR, now=NOW, machine=_machine(tmp_path, home))
    assert outcome.state == 'refused'
    assert f'no jmag under {home}' in outcome.message
    assert DOOR in outcome.message


def test_a_binary_whose_probe_reads_no_version_is_refused(tmp_path: Path, stamps: dict[Path, str]) -> None:
    home = tmp_path / 'JMAG-Designer25.2'
    del stamps[_designer(home, stamps)]  # the probe reads no file version
    outcome = vendor.record(JMAG, FLOOR, now=NOW, machine=_machine(tmp_path, home))
    assert outcome.state == 'refused'
    assert 'refused by its probe' in outcome.message
    assert DOOR in outcome.message


def test_no_declared_home_is_refused_naming_the_host_file_line(tmp_path: Path) -> None:
    outcome = vendor.record(JMAG, FLOOR, now=NOW, machine=_machine(tmp_path, None))
    assert outcome.state == 'refused'
    assert '[dev.vendors.jmag] home' in outcome.message
    assert 'T.toml' in outcome.message
    assert DOOR in outcome.message


def test_the_unfilled_placeholder_is_no_home(tmp_path: Path) -> None:
    machine = _machine(tmp_path, Path(vendor.UNFILLED))
    assert vendor.declared_home('jmag', machine=machine).home is None


def test_a_verified_home_the_host_file_does_not_record_is_refused_with_its_line(
    tmp_path: Path, stamps: dict[Path, str]
) -> None:
    """The door never writes the host file: a home it verifies but the host file lacks is refused, line given."""
    home = tmp_path / 'D' / 'JMAG-Designer25.2'
    _designer(home, stamps)
    outcome = vendor.record(JMAG, FLOOR, home=home, now=NOW, machine=_machine(tmp_path, None))
    assert outcome.state == 'refused'
    assert f'home = "{home.as_posix()}"' in outcome.message
    assert 'pull request' in outcome.message


def test_an_ansys_engine_is_probed_under_the_ansys_home(tmp_path: Path) -> None:
    home = tmp_path / 'ANSYS Inc'
    for release in ('v242', 'v251'):
        exe = home / release / 'ansys' / 'bin' / 'winx64' / f'ANSYS{release[1:]}.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'MZ')
        exe.chmod(0o755)
    mapdl = Tool('mapdl', 'user-installed', 'ansys')
    checks = {'mapdl': replace(toolchecks.CHECKS['mapdl'], within=toolchecks.ansys_within('mapdl', platform='win32'))}
    outcome = vendor.record(
        mapdl, Requirement('2025 R1', 'x'), checks=checks, now=NOW, machine=_machine(tmp_path, home, vendor_key='ansys')
    )
    assert outcome.state == 'recorded', outcome.message
    assert '2025 R1' in outcome.message, 'the newest release directory under the home is the one probed'


def test_the_cli_records_and_never_downloads(
    tmp_path: Path, stamps: dict[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / 'JMAG-Designer25.2'
    _designer(home, stamps)
    machine = tmp_path / 'motronics.toml'
    machine.write_text(
        f'[devhost]\nhost = "T"\nsource = "{(tmp_path / "hosts-config").as_posix()}"\n', encoding='utf-8'
    )
    _machine(tmp_path, home)
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(machine))

    def never(*_args: object) -> None:
        msg = 'a user-installed tool is never downloaded or run'
        raise AssertionError(msg)

    monkeypatch.setattr(door, 'download', never)
    monkeypatch.setattr(door, 'launch_installer', never)
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'pyproject.toml').write_text(
        '[tool.lab_commons.tools]\njmag = {floor = "25.2", reason = "file jobs"}\n', encoding='utf-8'
    )
    assert main(['install', 'jmag', '--repo', str(repo)]) == 0
    assert main(['install', 'jmag', '--repo', str(repo), '--version', '25.1']) == 1, 'below the declared floor'
    assert main(['install', 'jmag', '--repo', str(repo), '--installer', str(home / 'designer.exe')]) == 2, (
        '--installer belongs to a managed tool'
    )
    assert main(['install', 'femm', '--repo', str(repo), '--home', str(home)]) == 2, '--home is user-installed'
