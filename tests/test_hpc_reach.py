"""How this machine reaches a cluster is checked before any ssh to its login host (lab_commons.hpc.reach)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from lab_commons.hpc import cluster, reach
from lab_commons.hpc.reach import ReachRefused, require
from lab_commons.hpc.sshgate import Gate

HOST = 'alice@login1.ada.example'
CLUSTERS = {'clusters': {'ada': {'hosts': ['login1.ada.example', 'login2.ada.example']}}}
DIRECT = ('WS1', 'WS2', 'WS5')


def _hpc(by: str | None) -> dict[str, Any]:
    rows = {} if by is None else {'cluster_reach': {'ada': {'reach': by, 'direct': list(DIRECT)}}}
    return {**CLUSTERS, **rows}


def test_a_direct_machine_reaches_the_cluster() -> None:
    assert require(HOST, _hpc('direct')) == reach.Reach('ada', 'direct', DIRECT)


def test_an_undeclared_reach_is_refused_naming_the_declaration() -> None:
    with pytest.raises(ReachRefused, match=r'declares no reach.*\[dev\] hpc_reach.*no ssh was attempted'):
        require(HOST, _hpc(None))


def test_a_host_no_cluster_lists_is_never_checked() -> None:
    assert require('bob@elsewhere.example', _hpc(None)) is None


def test_a_reach_outside_the_set_is_refused() -> None:
    with pytest.raises(ValueError, match="reach = 'campus' is not one of"):
        require(HOST, _hpc('campus'))


@pytest.mark.parametrize('by', ['direct', 'vpn'])
def test_a_silent_host_names_the_vpn_only_over_a_vpn(by: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach, 'section', lambda _name: _hpc(by))

    def silent(argv: list[str], _stdin: str | None, _timeout: float) -> str:
        raise cluster.Unreachable(f'ssh could not reach {argv[-2]}: timed out')

    monkeypatch.setattr(cluster, '_completed', silent)
    with pytest.raises(cluster.Unreachable) as caught:
        cluster.ssh_runner(HOST, gate=Gate(HOST, root=tmp_path))('true', None)
    said = str(caught.value)
    vpn = 'this machine reaches ada only over VPN: connect the VPN, or run on a direct host (WS1, WS2, WS5)'
    assert (vpn in said) is (by == 'vpn')  # the planted control: a direct machine is never told about a VPN


def test_a_vpn_timeout_names_the_vpn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach, 'section', lambda _name: _hpc('vpn'))

    def hangs(argv: list[str], _stdin: str | None, timeout: float) -> str:
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(cluster, '_completed', hangs)
    with pytest.raises(cluster.Unreachable, match='only over VPN'):
        cluster.ssh_runner(HOST, gate=Gate(HOST, root=tmp_path))('true', None)


def test_an_undeclared_reach_runs_no_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach, 'section', lambda _name: _hpc(None))
    ran: list[list[str]] = []
    monkeypatch.setattr(cluster, '_completed', lambda argv, _s, _t: ran.append(argv) or '')
    with pytest.raises(ReachRefused):
        cluster.ssh_runner(HOST)
    assert ran == []
