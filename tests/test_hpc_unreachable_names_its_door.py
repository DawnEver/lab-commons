"""An unreachable cluster is a one-line refusal naming its cause and door, never a traceback (EVERY-WALL-TEACHES)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lab_commons.hpc import __main__ as cli
from lab_commons.hpc.cluster import Unreachable
from lab_commons.hpc.reach import ReachRefused


def _door(outcome: Exception | None) -> Callable[[list[str]], int]:
    def door(_argv: list[str]) -> int:
        if outcome is not None:
            raise outcome
        return 0

    return door


@pytest.mark.parametrize(
    'refused',
    [
        Unreachable(
            'no login host answered -- this machine reaches ada only over VPN: connect the VPN, or run on a '
            'direct host (WS1)'
        ),
        ReachRefused('a login host of ada and this machine declares no reach for it: declare [dev] hpc_reach'),
    ],
)
def test_an_unreachable_cluster_refuses_in_one_line_naming_its_door(
    refused: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The door is the runner's (lab_commons.hpc.reach): the CLI says it as it is, in one line."""
    monkeypatch.setattr(cli.clean, 'main', _door(refused))
    code = cli.main(['clean', '--root', '/x', 'a'])
    err = capsys.readouterr().err.strip()
    assert code == cli.UNREACHABLE_EXIT
    assert err == f'hpc refused: {refused}'


def test_a_reachable_cluster_runs_its_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: a verb that reaches the cluster returns its own exit status."""
    monkeypatch.setattr(cli.clean, 'main', _door(None))
    assert cli.main(['clean', '--root', '/x', 'a']) == 0
