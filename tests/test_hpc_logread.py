"""``lab_commons.hpc log``: a read-only tail of a file under the account's ``~/ci``, through the gated ssh door."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lab_commons.hpc import logread
from lab_commons.hpc.cluster import Unreachable

if TYPE_CHECKING:
    from lab_commons.hpc.cluster import Runner

DEST = 'alice@hpclogin01.example.org'


def _run(argv: list[str], answer: str = 'line\n') -> tuple[int, list[str]]:
    sent: list[str] = []

    def at(dest: str, timeout: float) -> Runner:
        assert dest == DEST
        assert timeout > 0
        return lambda command, _stdin: sent.append(command) or answer

    return logread.main(argv, at=at), sent


def test_a_ci_log_is_tailed_with_its_line_count() -> None:
    """The planted control of every refusal below."""
    code, sent = _run(['--at', DEST, '--lines', '50', '/gpfs01/home/alice/ci/envs/abc.log'])
    assert code == 0
    assert len(sent) == 1
    assert 'tail -n 50' in sent[0]
    assert '"$HOME/ci/"' in sent[0]  # the remote side confines the real path to ~/ci


def test_a_path_relative_to_ci_is_read_there() -> None:
    _, sent = _run(['--at', DEST, 'envs/abc.log'])
    assert 'realpath -e -- envs/abc.log' in sent[0]


@pytest.mark.parametrize('path', ['../.ssh/id_rsa', '/etc/passwd', '/gpfs01/home/alice/ci/../.ssh/id_rsa', ''])
def test_a_path_outside_ci_is_refused_before_ssh(path: str) -> None:
    code, sent = _run(['--at', DEST, path])
    assert code == 2
    assert sent == []


def test_the_line_count_is_bounded() -> None:
    code, sent = _run(['--at', DEST, '--lines', str(logread.MAX_LINES + 1), 'envs/abc.log'])
    assert code == 2
    assert sent == []


def test_a_file_the_remote_refuses_is_a_refusal() -> None:
    code, _ = _run(['--at', DEST, 'envs/abc.log'], answer=logread.OUTSIDE + '\n')
    assert code == 2


def test_an_unreachable_host_is_one_line_not_a_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    def at(_dest: str, _timeout: float) -> Runner:
        def run(_command: str, _stdin: str | None) -> str:
            msg = 'ssh could not reach alice@hpclogin01.example.org: Connection timed out'
            raise Unreachable(msg)

        return run

    assert logread.main(['--at', DEST, 'envs/abc.log'], at=at) == 3
    assert 'unreachable' in capsys.readouterr().err
