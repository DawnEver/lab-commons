"""``python -m lab_commons.hpc deploy`` against a fake home, a fake ``crontab``, ``git``, ``uv`` and shell.

Each fake is a tiny Python program run through :data:`sys.executable`, so the test is the same on every
box: it logs its argv to ``calls.log`` and keeps its state in files under the test's temporary directory.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc import deploy, deploy_spec, deploy_steps
from lab_commons.hpc.deploy_steps import Tools
from lab_commons.hpc.grants import load_grants

URL: Final = 'https://forge.example/team'
LAB: Final = ('--repo', f'lab-commons={URL}/lab-commons.git@trunk')
GRANT: Final = ('--partition', 'shortq', '--partition', 'defq')
DECLARE: Final = (
    '--host', 'login02', *LAB, '--repo', f'alpha={URL}/alpha.git@trunk', '--repo', f'beta={URL}/beta.git@trunk', *GRANT
)  # fmt: skip
SACCT: Final = 'acct-free|cpu=96,mem=360G\nacct-free|cpu=96,mem=360G\n'

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
if args[:3] == ['config', '--global', '--get']:
    helper = os.environ.get('FAKE_GIT_HELPER', 'store')  # 'unset': an empty variable does not survive Windows
    sys.stdout.write('' if helper == 'unset' else helper + '\n')
    sys.exit(1 if helper == 'unset' else 0)
if 'rev-parse' in args and args[-1] == 'HEAD':
    head = pathlib.Path(args[args.index('-C') + 1], '.git', 'HEAD_SHA')
    sys.stdout.write(head.read_text() if head.exists() else 'a' * 40)
if args[0] == 'ls-remote':
    sys.exit(128 if any(d in args[1] for d in os.environ.get('FAKE_GIT_DENY', '').split(',') if d) else 0)
if args[0] == 'clone':
    target = pathlib.Path(args[-1])
    (target / '.git').mkdir(parents=True)
    (target / 'pyproject.toml').write_text('[tool.lab_commons.hpc]\ninstall = "uv sync --extra dev"\n')
"""

FAKE_ANSWER: Final = r"""
import os, pathlib, sys
with pathlib.Path(sys.argv[2]).open('a') as f: f.write(sys.argv[3] + ' ' + ' '.join(sys.argv[4:]) + '\n')
sys.stdout.write(os.environ.get(sys.argv[1], ''))
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
    refused_api: dict[str, str]

    def run(self, *argv: str, host: str = 'login02.int.cluster') -> int:
        return deploy.deploy(
            [*argv, '--config', str(self.machine)],
            home=self.home,
            hostname=host,
            tools=self.tools,
            api=lambda url, _home: next((why for name, why in self.refused_api.items() if f'/{name}.git' in url), ''),
        )

    def logged(self) -> str:
        return self.calls.read_text(encoding='utf-8') if self.calls.exists() else ''


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding='utf-8')
    return path


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.git-credentials').write_text('https://me:t@forge.example\n', encoding='utf-8')
    (home / '.git-credentials').chmod(0o600)
    monkeypatch.setenv('FAKE_ID', 'alice')
    monkeypatch.setenv('FAKE_SACCT', SACCT)
    monkeypatch.setattr(deploy, '_which', lambda name, **_kw: f'/x/{name}')
    calls = tmp_path / 'calls.log'
    crontab = tmp_path / 'crontab.txt'
    logger = str(_write(tmp_path / 'logger.py', FAKE_LOGGER))
    answer = str(_write(tmp_path / 'answer.py', FAKE_ANSWER))
    tools = Tools(
        git=(sys.executable, str(_write(tmp_path / 'git.py', FAKE_GIT)), str(calls)),
        crontab=(sys.executable, str(_write(tmp_path / 'crontab.py', FAKE_CRONTAB)), str(crontab)),
        uv=(sys.executable, logger, str(calls), 'uv'),
        sh=(sys.executable, logger, str(calls), 'sh'),
        id=(sys.executable, answer, 'FAKE_ID', str(calls), 'id'),
        sacctmgr=(sys.executable, answer, 'FAKE_SACCT', str(calls), 'sacctmgr'),
    )
    return Box(home, tmp_path / 'machine' / 'config.toml', crontab, calls, tools, {})


def test_twice_gives_one_marked_line_per_repo_and_keeps_the_users_own(box: Box) -> None:
    _write(box.crontab, '0 1 * * * echo mine\n')
    assert box.run(*DECLARE) == 0
    assert box.run() == 0  # the second run reads the declaration the first one stored
    lines = box.crontab.read_text(encoding='utf-8').splitlines()
    assert lines[0] == '0 1 * * * echo mine'
    marked = [line for line in lines if deploy_spec.MARK in line]
    assert {line.rsplit(deploy_spec.MARK, 1)[1] for line in marked} == {'lab-commons', 'alpha', 'beta'}
    assert len(marked) == 3
    alpha = next(line for line in marked if line.endswith('alpha'))
    assert alpha.startswith('*/5 * * * * ')
    assert 'flock -n' in alpha
    assert 'lab_commons.dev.forge serve --mode integrate' in alpha
    calls = box.logged()
    assert calls.count('git clone') == 3  # the second run updates, never re-clones
    assert 'fetch -q origin +refs/heads/trunk' in calls


def test_the_declaration_is_stored_once_and_a_disagreeing_one_is_refused(
    box: Box, capsys: pytest.CaptureFixture[str]
) -> None:
    assert box.run(*DECLARE) == 0
    stored = deploy_spec.load(box.machine)
    assert stored is not None
    assert stored.host == 'login02'
    assert [r.name for r in stored.repos] == ['lab-commons', 'alpha', 'beta']
    assert stored.partitions == ('shortq', 'defq')
    assert box.run(*DECLARE) == 0
    assert box.machine.read_text(encoding='utf-8').count('[deploy]') == 1
    assert box.run('--host', 'login02', *LAB, '--repo', f'gamma={URL}/gamma.git@trunk', *GRANT) == 2
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
    assert [p.name for p in box.home.iterdir()] == ['.git-credentials'], 'only the token the fixture stored'
    reads = {'git ls-remote', 'git config', 'crontab -l', 'id -un', 'sacctmgr -nP'}
    assert {' '.join(line.split()[:2]) for line in box.logged().splitlines()} <= reads
    for action in ('declare [deploy]', 'git clone', 'uv sync --extra dev', 'crontab', 'tick'):
        assert action in out
    assert '[[hpc.grant]]' in out
    assert out.count('[dry-run]') >= 7


def test_the_venv_is_built_once_per_declaration(box: Box) -> None:
    assert box.run(*DECLARE) == 0
    assert box.run() == 0
    assert box.logged().count('uv sync --extra dev') == 3  # one per repo, not per run


def _overlays(box: Box) -> list[str]:
    return [line for line in box.logged().splitlines() if '--reinstall-package lab-commons' in line]


def test_every_served_venv_runs_the_lab_commons_this_host_deployed(box: Box) -> None:
    """Not the sha its own pyproject pins. PLANTED CONTROL: lab-commons' own venv gets no overlay."""
    assert box.run(*DECLARE) == 0
    overlays = _overlays(box)
    lab = (box.home / 'src' / 'lab-commons').as_uri()
    for name in ('alpha', 'beta'):
        python = box.home / 'src' / name / '.venv' / 'bin' / 'python'
        line = f'uv pip install --no-config --python {python} --reinstall-package lab-commons lab-commons @ {lab}'
        assert line in overlays
    assert len(overlays) == 2, 'lab-commons itself is installed by its own install'
    assert box.run() == 0
    assert len(_overlays(box)) == 2, 'an unchanged lab-commons reinstalls nothing'
    (box.home / 'src' / 'lab-commons' / '.git' / 'HEAD_SHA').write_text('b' * 40, encoding='utf-8')
    assert box.run() == 0
    assert len(_overlays(box)) == 4, 'a lab-commons update is in every served env key'


def test_a_deployment_that_does_not_serve_lab_commons_is_refused(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert box.run('--host', 'login02', '--repo', f'alpha={URL}/alpha.git@trunk', *GRANT) == 2
    assert '--repo lab-commons=' in capsys.readouterr().err
    assert box.logged() == ''


def test_a_token_the_api_refuses_is_refused_before_anything_changes(
    box: Box, capsys: pytest.CaptureFixture[str]
) -> None:
    """ls-remote passed on Ada and every tick then died at GET /user. PLANTED CONTROL: the same run, API allowed."""
    box.refused_api['beta'] = 'GET /api/v1/user refused'
    assert box.run(*DECLARE) == 2
    err = capsys.readouterr().err
    assert 'beta: GET /api/v1/user refused' in err
    assert 'auth login' in err
    assert 'clone' not in box.logged()
    box.refused_api.clear()
    assert box.run(*DECLARE) == 0


def test_an_unset_credential_helper_is_refused_naming_the_fix(
    box: Box, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('FAKE_GIT_HELPER', 'unset')
    assert box.run(*DECLARE) == 2
    assert 'git config --global credential.helper store' in capsys.readouterr().err
    assert 'ls-remote' not in box.logged()


def test_a_missing_tool_is_installed_or_refused_by_name(
    box: Box, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uv and cargo installers are the official ones, into ~/.local and ~/.cargo; cc cannot, so it is refused."""
    monkeypatch.setattr(deploy, '_which', lambda name, **_kw: None if name in {'uv', 'cargo'} else f'/x/{name}')
    monkeypatch.setattr(deploy, 'declared', lambda _c: deploy_steps.Builds(install='uv sync', needs=('cargo',)))
    assert box.run(*DECLARE) == 0
    installs = [line for line in box.logged().splitlines() if line.startswith('sh curl')]
    assert len(installs) == 2
    assert 'UV_NO_MODIFY_PATH=1' in installs[0]
    assert '--no-modify-path' in installs[1]
    monkeypatch.setattr(deploy, 'declared', lambda _c: deploy_steps.Builds(install='uv sync', needs=('cc',)))
    monkeypatch.setattr(deploy, '_which', lambda name, **_kw: None if name == 'cc' else f'/x/{name}')
    assert box.run() == 2
    err = capsys.readouterr().err
    assert 'cc' in err
    assert '.local/bin' in err


def test_the_deploy_writes_this_accounts_own_grant_once(box: Box) -> None:
    """PLANTED CONTROL: an account that already holds a grant keeps it, untouched."""
    assert box.run(*DECLARE) == 0
    (grant,) = load_grants(tomllib.loads(box.machine.read_text(encoding='utf-8'))['hpc']).grants
    assert grant.account == ('alice', 'acct-free')
    assert grant.cpus == 96
    assert grant.hosts == ('login02.int.cluster',)
    assert grant.partitions == ('shortq', 'defq')
    before = box.machine.read_text(encoding='utf-8')
    assert box.run() == 0
    assert box.machine.read_text(encoding='utf-8') == before


def test_two_slurm_accounts_are_refused_until_one_is_declared(
    box: Box, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('FAKE_SACCT', 'acct-int|cpu=96\nacct-free|cpu=96,mem=1G\n')
    assert box.run(*DECLARE) == 2
    err = capsys.readouterr().err
    assert 'acct-int' in err
    assert '--slurm-account' in err
    assert not box.machine.exists() or '[[hpc.grant]]' not in box.machine.read_text(encoding='utf-8')
    box.machine.unlink(missing_ok=True)
    assert box.run(*DECLARE, '--slurm-account', 'acct-free') == 0
    (grant,) = load_grants(tomllib.loads(box.machine.read_text(encoding='utf-8'))['hpc']).grants
    assert grant.slurm_account == 'acct-free'


def test_the_rendered_declaration_reads_back(tmp_path: Path) -> None:
    spec = deploy_spec.Deployment(
        'h', 'src', '3.13', ('--mode', 'integrate'), (deploy_spec.Repo('a', 'https://x/a"b', 'r'),), 'acct', ('q',)
    )
    path = _write(tmp_path / 'config.toml', deploy_spec.render(spec))
    assert deploy_spec.load(path) == spec


def test_the_cron_path_carries_where_slurm_resolved_at_deploy_time(
    box: Box, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cron's PATH has no site Slurm: a tick said "this box has no sbatch". PLANTED CONTROL: no sbatch, no deploy."""
    slurm = {'sbatch': '/opt/slurm/23.02.6/bin/sbatch', 'sacct': '/opt/slurm/23.02.6/bin/sacct'}
    slurm['squeue'] = '/opt/slurm/23.02.6/bin/squeue'
    monkeypatch.setattr(deploy, '_which', lambda name, **_kw: slurm.get(name, f'/x/{name}'))
    assert box.run(*DECLARE) == 0
    marked = [line for line in box.crontab.read_text(encoding='utf-8').splitlines() if deploy_spec.MARK in line]
    assert all(':/bin:' + str(Path('/opt/slurm/23.02.6/bin')) + ';' in line for line in marked), marked[0]
    del slurm['sbatch']
    monkeypatch.setattr(deploy, '_which', lambda name, **_kw: None if name == 'sbatch' else slurm.get(name, '/x/y'))
    assert box.run() == 2
    assert 'sbatch' in capsys.readouterr().err
