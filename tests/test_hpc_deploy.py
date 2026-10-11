"""``python -m lab_commons.hpc deploy`` against a fake home, a fake ``crontab``, ``git``, ``uv`` and shell.

Each fake is a tiny Python program run through :data:`sys.executable`, so the test is the same on every
box: it logs its argv to ``calls.log`` and keeps its state in files under the test's temporary directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc import deploy

URL: Final = 'https://forge.example/team'

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


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding='utf-8')
    return path


@pytest.fixture
def box(tmp_path: Path) -> dict[str, object]:
    home = tmp_path / 'home'
    home.mkdir()
    calls = tmp_path / 'calls.log'
    config = _write(
        tmp_path / 'pyproject.toml',
        f"""
[tool.lab_commons.deploy]
host = "login02"
root = "src"
python = "3.13"
serve = ["--mode", "integrate"]
repos = [
  {{ name = "alpha", url = "{URL}/alpha.git", ref = "integrate/main" }},
  {{ name = "beta", url = "{URL}/beta.git", ref = "integrate/main" }},
]
""",
    )
    crontab_py = _write(tmp_path / 'crontab.py', FAKE_CRONTAB)
    git_py = _write(tmp_path / 'git.py', FAKE_GIT)
    logger = _write(tmp_path / 'logger.py', FAKE_LOGGER)
    tools = deploy.Tools(
        git=(sys.executable, str(git_py), str(calls)),
        crontab=(sys.executable, str(crontab_py), str(tmp_path / 'crontab.txt')),
        uv=(sys.executable, str(logger), str(calls), 'uv'),
        sh=(sys.executable, str(logger), str(calls), 'sh'),
    )
    return {'home': home, 'calls': calls, 'config': config, 'tools': tools, 'tmp': tmp_path}


def _deploy(box: dict[str, object], *argv: str, host: str = 'login02.int.cluster') -> int:
    return deploy.deploy(
        [*argv, '--config', str(box['config'])],
        home=box['home'],  # type: ignore[arg-type]
        hostname=host,
        tools=box['tools'],  # type: ignore[arg-type]
    )


def _crontab(box: dict[str, object]) -> str:
    return (box['tmp'] / 'crontab.txt').read_text(encoding='utf-8')  # type: ignore[operator]


def test_twice_gives_one_marked_line_per_repo_and_keeps_the_users_own(box: dict[str, object]) -> None:
    _write(box['tmp'] / 'crontab.txt', '0 1 * * * echo mine\n')  # type: ignore[operator]
    assert _deploy(box) == 0
    assert _deploy(box) == 0
    lines = _crontab(box).splitlines()
    assert lines[0] == '0 1 * * * echo mine'
    marked = [line for line in lines if deploy.MARK in line]
    assert len(marked) == 2
    assert {line.rsplit(deploy.MARK, 1)[1] for line in marked} == {'alpha', 'beta'}
    alpha = next(line for line in marked if line.endswith('alpha'))
    assert alpha.startswith('*/5 * * * * ')
    assert 'flock -n' in alpha
    assert 'lab_commons.dev.forge serve --mode integrate' in alpha
    calls = Path(box['calls']).read_text(encoding='utf-8')  # type: ignore[arg-type]
    assert calls.count('git clone') == 2  # the second run updates, never re-clones
    assert 'git -C' in calls
    assert 'fetch' in calls


def test_remove_deletes_only_the_marked_lines(box: dict[str, object]) -> None:
    _write(box['tmp'] / 'crontab.txt', '0 1 * * * echo mine\n')  # type: ignore[operator]
    assert _deploy(box) == 0
    assert _deploy(box, '--remove') == 0
    assert _crontab(box) == '0 1 * * * echo mine\n'


def test_the_wrong_host_is_refused_naming_the_declared_one(
    box: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _deploy(box, host='login01.int.cluster') == 2
    err = capsys.readouterr().err
    assert 'login02' in err
    assert '[tool.lab_commons.deploy]' in err
    assert not Path(box['calls']).exists()  # type: ignore[arg-type]


def test_a_credential_failure_names_the_remedy_and_changes_nothing(
    box: dict[str, object], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('FAKE_GIT_DENY', 'beta')
    assert _deploy(box) == 2
    err = capsys.readouterr().err
    assert 'beta' in err
    assert '~/.git-credentials' in err
    calls = Path(box['calls']).read_text(encoding='utf-8')  # type: ignore[arg-type]
    assert 'clone' not in calls
    assert not (box['tmp'] / 'crontab.txt').exists()  # type: ignore[operator]


def test_dry_run_touches_nothing_and_prints_every_action(
    box: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _deploy(box, '--dry-run') == 0
    out = capsys.readouterr().out
    assert not (box['tmp'] / 'crontab.txt').exists()  # type: ignore[operator]
    assert list(Path(box['home']).iterdir()) == []  # type: ignore[arg-type]
    calls = Path(box['calls']).read_text(encoding='utf-8')  # type: ignore[arg-type]
    assert {line.split()[0] + ' ' + line.split()[1] for line in calls.splitlines()} <= {
        'git ls-remote',
        'crontab -l',
    }
    for action in ('git clone', 'uv sync --extra dev', 'crontab', 'tick'):
        assert action in out
    assert out.count('[dry-run]') >= 6


def test_the_venv_is_built_once_per_declaration(box: dict[str, object]) -> None:
    assert _deploy(box) == 0
    assert _deploy(box) == 0
    calls = Path(box['calls']).read_text(encoding='utf-8')  # type: ignore[arg-type]
    assert calls.count('uv sync --extra dev') == 2  # one per repo, not per run


def test_cmake_comes_from_uv_only_when_declared_and_absent() -> None:
    assert deploy.cmake_needed([('a', ('cargo', 'cmake')), ('b', ('cc',))], which=lambda _n: None) == ['a']
    assert deploy.cmake_needed([('a', ('cmake',))], which=lambda _n: '/usr/bin/cmake') == []
