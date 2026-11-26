"""NO-LOCAL-HEAVY: a heavy entry refuses on a declared workstation BEFORE any work, and proceeds where declared."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lab_commons.box import heavy
from lab_commons.box.heavy import CAPABILITY, EXIT_REFUSED, RULE, WORKSTATION, HeavyRefused, refusal, require
from lab_commons.config import CONFIG_ENV
from lab_commons.hostenv import HEAVY_APPROVED_ENV, Host

_WORKSTATION = {WORKSTATION: {}}
_COMPUTE = {CAPABILITY: {}}


def _machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *tables: str) -> None:
    path = tmp_path / 'machine.toml'
    path.write_text(''.join(f'[capabilities.{name}]\n' for name in tables), encoding='utf-8')
    monkeypatch.setenv(CONFIG_ENV, str(path))
    monkeypatch.delenv(HEAVY_APPROVED_ENV, raising=False)


def test_a_declared_workstation_refuses_naming_cause_host_and_door() -> None:
    why = refusal('python -m x.driver', capabilities=_WORKSTATION, host=Host(), hostname='box-a')
    assert why is not None
    assert why.startswith(f'{RULE}: python -m x.driver')
    assert 'box-a' in why, 'the refusal names the host'
    assert f'[capabilities.{CAPABILITY}]' in why
    assert 'dev_lab.dev.forge request' in why, 'the forge door'
    assert 'lab_commons.hpc submit' in why, 'the HPC door'
    assert HEAVY_APPROVED_ENV in why, "the user's override is named"


@pytest.mark.parametrize(
    'caps',
    [_COMPUTE, {WORKSTATION: {}, CAPABILITY: {}}, {}, {'jmag': {}}],
    ids=['compute-host', 'workstation-with-capability', 'no-machine-file', 'undeclared-role'],
)
def test_a_host_that_is_no_bare_workstation_proceeds(caps: dict[str, dict[str, object]]) -> None:
    assert refusal('python -m x.driver', capabilities=caps, host=Host(), hostname='h') is None


def test_only_the_users_words_override_a_workstation() -> None:
    assert refusal('e', capabilities=_WORKSTATION, host=Host(heavy_approved='run it once, Tuesday')) is None
    assert refusal('e', capabilities=_WORKSTATION, host=Host(heavy_approved='   ')) is not None, 'blank is no consent'


def test_the_machine_file_is_read_when_nothing_is_planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PLANTED CONTROL, both answers through the real reader: the workstation file refuses, the compute file runs."""
    _machine(tmp_path, monkeypatch, WORKSTATION)
    assert refusal('e') is not None
    _machine(tmp_path, monkeypatch, WORKSTATION, CAPABILITY)
    assert refusal('e') is None


def test_require_refuses_before_the_work_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The work after ``require`` never runs on a workstation, and runs on a compute host."""
    started: list[str] = []

    def entry() -> None:
        require('python -m x.driver')
        started.append('work')

    _machine(tmp_path, monkeypatch, WORKSTATION)
    with pytest.raises(HeavyRefused) as refused:
        entry()
    assert refused.value.code == EXIT_REFUSED
    assert str(refused.value).startswith(RULE)
    assert started == [], 'nothing started'
    assert RULE in capsys.readouterr().err
    _machine(tmp_path, monkeypatch, CAPABILITY)
    entry()
    assert started == ['work']


def test_the_check_command_exits_two_on_a_workstation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _machine(tmp_path, monkeypatch, WORKSTATION)
    assert heavy.main(['check', 'cargo run --bin x']) == EXIT_REFUSED
    _machine(tmp_path, monkeypatch, CAPABILITY)
    assert heavy.main(['check', 'cargo run --bin x']) == 0


def test_the_module_door_runs_as_a_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What a non-Python caller sees: exit 2 and the refusal on stderr."""
    _machine(tmp_path, monkeypatch, WORKSTATION)
    done = subprocess.run(
        [sys.executable, '-m', 'lab_commons.box.heavy', 'check', 'x'],
        capture_output=True, text=True, encoding='utf-8', check=False, timeout=60,
    )  # fmt: skip
    assert done.returncode == EXIT_REFUSED
    assert RULE in done.stderr


def test_the_refusal_fits_a_refusals_budget() -> None:
    """Two lines of at most 320 characters (a refusal's budget, with the guard's details line the third)."""
    long_entry = 'python -m pkg.driver ' + 'x' * 400
    why = refusal(long_entry, capabilities=_WORKSTATION, host=Host(), hostname='a-long-host-name-01')
    assert why is not None
    lines = why.split('\n')
    assert len(lines) == 2
    assert max(map(len, lines)) <= 320, [len(line) for line in lines]
