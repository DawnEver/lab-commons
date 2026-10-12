"""``python -m lab_commons.hpc deploy`` against a fake home, a fake ``crontab``, ``git``, ``uv`` and shell.

Each fake is a tiny Python program run through :data:`sys.executable`, so the test is the same on every
box: it logs its argv to ``calls.log`` and keeps its state in files under the test's temporary directory.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc import deploy, deploy_spec
from lab_commons.hpc.deploy_steps import Tools

URL: Final = 'https://forge.example/team'
DECLARE: Final = ('--host', 'login02', '--repo', f'alpha={URL}/alpha.git@trunk', '--repo', f'beta={URL}/beta.git@trunk')

FAKE_CRONTAB: Final = r"""
import pathlib, sys
state = pathlib.Path(sys.argv[1])
log = state.parent / 'calls.log'
with log.open('a') as f: f.write('crontab ' + ' '.join(sys.argv[2:]) + '\n')
if sys.argv[2] == '-l':
    if not state.exists():
        sys.stderr.write('no crontab for me\n'); sys.exit(1)
    sys.stdout.write(state.read_text())
elif sys.argv[2] == '-':
    state.write_text(sys.stdin.read())
"""

FAKE_GIT: Final = r"""
import os, pathlib, sys
log = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
with log.open('a') as f: f.write('git ' + ' '.join(args) + '\n')
if args[0] == 'ls-remote':
    sys.exit(128 if any(d in args[1] for d in os.environ.get('FAKE_GIT_DENY', '').split(',') if d) else 0)
if args[0] == 'clone':
    target = pathlib.Path(args[-1])
    (target / '.git').mkdir(parents=True)
    (target / 'pyproject.toml').write_text('[tool.lab_commons.hpc]\ninstall = "uv sync --extra dev"\n')
"""

FAKE_LOGGER: Final = r"""
import pathlib, sys
with pathlib.Path(sys.argv[1]).open('a') as f: f.write(sys.argv[2] + ' ' + ' '.join(sys.argv[3:]) + '\n')
"""


@dataclass(frozen=True)
class Box:
    """One fake login node: its home, machine file, crontab state and the log every fake appends to."""

    home: Path
    machine: Path
    crontab: Path
    calls: Path
    tools: Tools

    def run(self, *argv: str, host: str = 'login02.int.cluster') -> int:
        return deploy.deploy([*argv, '--config', str(self.machine)], home=self.home, hostname=host, tools=self.tools)

    def logged(self) -> str:
        return self.calls.read_text(encoding='utf-8') if self.calls.exists() else ''


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding='utf-8')
    return path


@pytest.fixture
def box(tmp_path: Path) -> Box:
    home = tmp_path / 'home'
    home.mkdir()
    calls = tmp_path / 'calls.log'
    crontab = tmp_path / 'crontab.txt'
    logger = str(_write(tmp_path / 'logger.py', FAKE_LOGGER))
    tools = Tools(
        git=(sys.executable, str(_write(tmp_path / 'git.py', FAKE_GIT)), str(calls)),
        crontab=(sys.executable, str(_write(tmp_path / 'crontab.py', FAKE_CRONTAB)), str(crontab)),
        uv=(sys.executable, logger, str(calls), 'uv'),
        sh=(sys.executable, logger, str(calls), 'sh'),
    )
    return Box(home, tmp_path / 'machine' / 'config.toml', crontab, calls, tools)


def test_twice_gives_one_marked_line_per_repo_and_keeps_the_users_own(box: Box) -> None:
    _write(box.crontab, '0 1 * * * echo mine\n')
    assert box.run(*DECLARE) == 0
    assert box.run() == 0  # the second run reads the declaration the first one stored
    lines = box.crontab.read_text(encoding='utf-8').splitlines()
    assert lines[0] == '0 1 * * * echo mine'
    marked = [line for line in lines if deploy_spec.MARK in line]
    assert {line.rsplit(deploy_spec.MARK, 1)[1] for line in marked} == {'alpha', 'beta'}
    assert len(marked) == 2
    alpha = next(line for line in marked if line.endswith('alpha'))
    assert alpha.startswith('*/5 * * * * ')
    assert 'flock -n' in alpha
    assert 'lab_commons.dev.forge serve --mode integrate' in alpha
    calls = box.logged()
    assert calls.count('git clone') == 2  # the second run updates, never re-clones
    assert 'fetch -q origin +refs/heads/trunk' in calls


def test_the_declaration_is_stored_once_and_a_disagreeing_one_is_refused(
    box: Box, capsys: pytest.CaptureFixture[str]
) -> None:
    assert box.run(*DECLARE) == 0
    stored = deploy_spec.load(box.machine)
    assert stored is not None
    assert stored.host == 'login02'
    assert [r.name for r in stored.repos] == ['alpha', 'beta']
    assert box.run(*DECLARE) == 0
    assert box.machine.read_text(encoding='utf-8').count('[deploy]') == 1
    assert box.run('--host', 'login02', '--repo', f'gamma={URL}/gamma.git@trunk') == 2
    assert str(box.machine) in capsys.readouterr().err


def test_an_undeclared_box_names_the_flags(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert box.run() == 2
    assert '--host <login host> --repo' in capsys.readouterr().err


def test_remove_deletes_only_the_marked_lines(box: Box) -> None:
    _write(box.crontab, '0 1 * * * echo mine\n')
    assert box.run(*DECLARE) == 0
    assert box.run('--remove') == 0
    assert box.crontab.read_text(encoding='utf-8') == '0 1 * * * echo mine\n'


def test_the_wrong_host_is_refused_naming_the_declared_one(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert box.run(*DECLARE, host='login01.int.cluster') == 2
    err = capsys.readouterr().err
    assert 'login02' in err
    assert '[deploy]' in err
    assert box.logged() == ''
    assert not box.machine.exists()


def test_a_credential_failure_names_the_remedy_and_changes_nothing(
    box: Box, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('FAKE_GIT_DENY', 'beta')
    assert box.run(*DECLARE) == 2
    err = capsys.readouterr().err
    assert 'beta' in err
    assert '~/.git-credentials' in err
    assert 'clone' not in box.logged()
    assert not box.crontab.exists()


def test_dry_run_touches_nothing_and_prints_every_action(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert box.run(*DECLARE, '--dry-run') == 0
    out = capsys.readouterr().out
    assert not box.crontab.exists()
    assert not box.machine.exists()
    assert list(box.home.iterdir()) == []
    assert {' '.join(line.split()[:2]) for line in box.logged().splitlines()} <= {'git ls-remote', 'crontab -l'}
    for action in ('declare [deploy]', 'git clone', 'uv sync --extra dev', 'crontab', 'tick'):
        assert action in out
    assert out.count('[dry-run]') >= 7


def test_the_venv_is_built_once_per_declaration(box: Box) -> None:
    assert box.run(*DECLARE) == 0
    assert box.run() == 0
    assert box.logged().count('uv sync --extra dev') == 2  # one per repo, not per run


def test_cmake_comes_from_uv_only_when_declared_and_absent() -> None:
    assert deploy_spec.cmake_needed([('a', ('cargo', 'cmake')), ('b', ('cc',))], which=lambda _n: None) == ['a']
    assert deploy_spec.cmake_needed([('a', ('cmake',))], which=lambda _n: '/usr/bin/cmake') == []


def test_the_rendered_declaration_reads_back(tmp_path: Path) -> None:
    spec = deploy_spec.Deployment(
        'h', 'src', '3.13', ('--mode', 'integrate'), (deploy_spec.Repo('a', 'https://x/a"b', 'r'),)
    )
    path = _write(tmp_path / 'config.toml', deploy_spec.render(spec))
    assert deploy_spec.load(path) == spec
