"""An unreachable cluster is a one-line refusal naming its cause and door, never a traceback (EVERY-WALL-TEACHES)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lab_commons.hpc import __main__ as cli
from lab_commons.hpc.cluster import Unreachable


def _door(outcome: Exception | None) -> Callable[[list[str]], int]:
    def door(_argv: list[str]) -> int:
        if outcome is not None:
            raise outcome
        return 0

    return door


def test_an_unreachable_cluster_refuses_in_one_line_naming_the_network_door(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.clean, 'main', _door(Unreachable('no login host answered: port 22 timed out')))
    code = cli.main(['clean', '--root', '/x', 'a'])
    err = capsys.readouterr().err.strip()
    assert code != 0
    assert len(err.splitlines()) == 1
    assert 'no login host answered' in err
    assert 'VPN' in err
    assert 're-run' in err


def test_a_reachable_cluster_runs_its_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: a verb that reaches the cluster returns its own exit status."""
    monkeypatch.setattr(cli.clean, 'main', _door(None))
    assert cli.main(['clean', '--root', '/x', 'a']) == 0
