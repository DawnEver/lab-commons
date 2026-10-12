"""A deployed host's serve fast-forwards its own lab-commons before claiming; a workstation's is never moved."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _forge_fake import git_out, run_git

from lab_commons.dev import servekit
from lab_commons.dev.forgealarm import RUN
from lab_commons.hpc.deploy_spec import Deployment, Repo, render

REF = 'integrate/main'


def _commit(repo: Path, text: str) -> str:
    (repo / 'kit.txt').write_text(text, encoding='utf-8')
    run_git(repo, 'add', 'kit.txt')
    run_git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', text)
    return git_out(repo, 'rev-parse', 'HEAD')


class _Host:
    """An account home with a deployed lab-commons checkout following *REF* of a local origin."""

    def __init__(self, tmp_path: Path) -> None:
        self.home, self.upstream = tmp_path / 'home', tmp_path / 'upstream'
        self.upstream.mkdir()
        run_git(self.upstream, 'init', '-q', '-b', REF)
        self.first = _commit(self.upstream, 'first')
        self.kit = self.home / 'src' / 'lab-commons'
        self.kit.parent.mkdir(parents=True)
        run_git(self.kit.parent, 'clone', '-q', '-b', REF, str(self.upstream), 'lab-commons')
        self.machine = tmp_path / 'config.toml'
        repos = (Repo('lab-commons', str(self.upstream), REF), Repo('r', 'https://f/o/r.git', REF))
        self.machine.write_text(render(Deployment('login1', 'src', '3.13', ('--mode', 'integrate'), repos)), 'utf-8')
        self.calls: list[list[str]] = []
        self.alarms: list = []
        self.served: list[list[str]] = []

    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Git runs for real; the reinstall and the re-exec are recorded and succeed."""
        if Path(argv[0]).name.startswith('git'):
            return subprocess.run(argv, check=bool(kwargs.pop('check', False)), **kwargs)
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')

    def current(self, *, running: Path | None = None, environ: dict[str, str] | None = None, machine=None) -> int:
        return servekit.current(
            ['--mode', 'integrate'],
            lambda rest: self.served.append(rest) or 0,
            home=self.home,
            machine_file=self.machine if machine is None else machine,
            environ=environ or {},
            run=self.run,
            fetch=lambda kit: run_git(kit, 'fetch', '-q', 'origin'),
            alarm=self.alarms.append,
            running=running or Path(sys.prefix),
        )


def test_an_unmoved_kit_serves_in_this_process(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    assert host.current() == 0
    assert (host.served, host.calls) == ([['--mode', 'integrate']], [])


def test_a_moved_kit_is_fast_forwarded_reinstalled_and_the_serve_re_executed(tmp_path: Path) -> None:
    """PLANTED (live 2026-10-12): the cluster served with a kit older than the declaration it read."""
    host = _Host(tmp_path)
    newer = _commit(host.upstream, 'capabilities')
    assert host.current() == 0
    assert git_out(host.kit, 'rev-parse', 'HEAD') == newer
    install, reexec = host.calls
    assert install[1:3] == ['pip', 'install']
    assert install[-1] == f'lab-commons @ {host.kit.as_uri()}'
    assert reexec == [sys.executable, '-m', 'lab_commons.dev.forge', 'serve', '--mode', 'integrate']
    assert host.served == [], 'this process ran the OLD code: only the re-executed serve may claim'


def test_a_venv_that_imports_the_checkout_itself_is_not_reinstalled(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    _commit(host.upstream, 'newer')
    assert host.current(running=host.kit / 'src' / 'lab_commons') == 0
    assert [argv[1:4] for argv in host.calls] == [['-m', 'lab_commons.dev.forge', 'serve']]


def test_the_re_executed_serve_does_not_update_again(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    _commit(host.upstream, 'newer')
    assert host.current(environ={servekit.CURRENT_VAR: 'x'}) == 0
    assert git_out(host.kit, 'rev-parse', 'HEAD') == host.first
    assert host.served == [['--mode', 'integrate']]


@pytest.mark.parametrize('spoil', ['dirty', 'branch', 'diverged'])
def test_a_kit_that_cannot_fast_forward_claims_nothing_and_alarms(tmp_path: Path, spoil: str) -> None:
    host = _Host(tmp_path)
    _commit(host.upstream, 'newer')
    if spoil == 'dirty':
        (host.kit / 'kit.txt').write_text('edited by hand', encoding='utf-8')
    elif spoil == 'branch':
        run_git(host.kit, 'switch', '-q', '-c', 'probe')
    else:
        _commit(host.kit, 'local only')
    assert host.current() == 1
    assert host.served == []
    assert host.calls == []
    assert [alarm.kind for alarm in host.alarms] == [RUN]


def test_a_host_with_no_deploy_declaration_is_never_moved(tmp_path: Path) -> None:
    """PLANTED CONTROL: a workstation's serving checkout is a human's; the same moved upstream is not followed."""
    host = _Host(tmp_path)
    _commit(host.upstream, 'newer')
    assert host.current(machine=tmp_path / 'absent.toml') == 0
    assert git_out(host.kit, 'rev-parse', 'HEAD') == host.first
    assert host.served == [['--mode', 'integrate']]
