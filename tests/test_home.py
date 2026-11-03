"""The machine home: one root per machine, its fixed parts, the repos table and the write roots it derives."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons import config, home


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / 'motronics-home'
    monkeypatch.setenv(home.HOME_ENV, str(root))
    monkeypatch.delenv('LAB_COMMONS_CONFIG', raising=False)
    return root


def _declare(body: str) -> None:
    home.config_path().parent.mkdir(parents=True, exist_ok=True)
    home.config_path().write_text(body, encoding='utf-8')


def test_the_home_has_its_fixed_parts(machine: Path) -> None:
    assert home.home() == machine
    assert home.config_path() == machine / 'config' / 'motronics.toml'
    assert {part: home.part(part) for part in home.PARTS} == {part: machine / part for part in home.PARTS}


@pytest.mark.usefixtures('machine')
def test_an_unknown_part_is_refused() -> None:
    with pytest.raises(ValueError, match='not a part of the machine home'):
        home.part('tmp')


def test_the_default_home_is_dot_motronics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(home.HOME_ENV, raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    assert home.home() == tmp_path / '.motronics'


def test_the_config_module_reads_the_home_file(machine: Path) -> None:
    assert config.config_path() == machine / 'config' / 'motronics.toml'


@pytest.mark.usefixtures('machine')
def test_repos_are_read_from_the_one_table(tmp_path: Path) -> None:
    _declare(f'[repos]\nlab-commons = "{(tmp_path / "lc").as_posix()}"\n')
    assert home.repos() == {'lab-commons': tmp_path / 'lc'}


@pytest.mark.usefixtures('machine')
def test_a_relative_repo_path_is_refused() -> None:
    _declare('[repos]\nlab-commons = "lc"\n')
    with pytest.raises(ValueError, match='absolute'):
        home.repos()


def test_write_roots_are_the_home_plus_each_repos_output_and_scratch(machine: Path, tmp_path: Path) -> None:
    repo = tmp_path / 'lc'
    _declare(f'[repos]\nlab-commons = "{repo.as_posix()}"\n')
    assert home.write_roots() == (machine, repo / 'output', repo / 'scratch')


def test_allowed_admits_inside_a_root_and_refuses_outside(machine: Path, tmp_path: Path) -> None:
    repo = tmp_path / 'lc'
    _declare(f'[repos]\nlab-commons = "{repo.as_posix()}"\n')
    assert home.allowed(repo / 'output' / 'logs' / 'x.log')
    assert home.allowed(machine / 'state' / 'forge-runs')
    assert not home.allowed(repo / 'src' / 'new.py')  # planted: inside the repo, outside output/scratch
    assert not home.allowed(tmp_path / 'tmp' / 'x')  # planted: a stray root
    assert not home.allowed(repo / 'output' / '..' / 'src' / 'x.py')  # planted: an escape by ..


@pytest.mark.usefixtures('machine')
def test_a_repos_worktree_shares_its_roots(tmp_path: Path) -> None:
    repo = tmp_path / 'lc'
    _declare(f'[repos]\nlab-commons = "{repo.as_posix()}"\n')
    worktree = repo / '.claude' / 'worktrees' / 'w'
    assert home.repo_of(worktree / 'src' / 'a.py') == worktree
    assert home.allowed(worktree / 'output' / 'x.log')
    assert not home.allowed(worktree / 'src' / 'new.py')


@pytest.mark.usefixtures('machine')
def test_the_temp_of_a_path_is_its_repos_output_tmp(tmp_path: Path) -> None:
    repo = tmp_path / 'lc'
    _declare(f'[repos]\nlab-commons = "{repo.as_posix()}"\n')
    assert home.repo_of(repo / 'src' / 'a.py') == repo
    assert home.temp_of(repo / 'src') == repo / 'output' / 'tmp'
    with pytest.raises(ValueError, match='no configured repo'):
        home.temp_of(tmp_path / 'elsewhere')  # planted: no repo context is refused, never given a fallback


@pytest.mark.usefixtures('machine')
def test_temp_environment_points_every_name_at_the_repos_output_tmp(tmp_path: Path) -> None:
    repo = tmp_path / 'lc'
    _declare(f'[repos]\nlab-commons = "{repo.as_posix()}"\n')
    env = home.temp_environment(repo)
    assert env == dict.fromkeys(home.TEMP_NAMES, str(repo / 'output' / 'tmp'))
    assert (repo / 'output' / 'tmp').is_dir()


@pytest.mark.usefixtures('machine')
def test_claude_homes_and_agent_repos_are_outside_the_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path / 'user'))
    _declare(f'[agent_repos]\ncc-config = "{(tmp_path / "cc").as_posix()}"\n')
    assert home.exempt(tmp_path / 'user' / '.claude' / 'settings.json')
    assert home.exempt(tmp_path / 'user' / '.claude-evans' / 'x')
    assert home.exempt(tmp_path / 'cc' / 'hooks' / 'a.js')
    assert not home.exempt(tmp_path / 'user' / '.claudette' / 'x')  # planted: a near-miss name
    assert not home.exempt(tmp_path / 'user' / 'Documents' / 'x')  # planted: an ordinary path


def test_the_managed_tool_root_is_one_function(machine: Path) -> None:
    assert home.tools() == machine / 'tools'


@pytest.mark.usefixtures('machine')
def test_the_tools_table_reads_its_two_keys_and_its_install_root_is_a_write_root(tmp_path: Path) -> None:
    target = tmp_path / 'Program Files'
    _declare(f'[tools]\ninstall_root = "{target.as_posix()}"\nmin_free_gb = 5\n')
    assert home.tools_table() == home.Tools(install_root=target, min_free_gb=5.0)
    assert target in home.write_roots()
    assert home.allowed(target / 'femm' / 'femm.exe')


@pytest.mark.usefixtures('machine')
def test_an_undeclared_tools_table_has_no_install_root() -> None:
    assert home.tools_table() == home.Tools(install_root=None, min_free_gb=None)
    assert not home.allowed(Path('C:/Program Files/femm/femm.exe'))  # planted: no install root declared


@pytest.mark.usefixtures('machine')
@pytest.mark.parametrize(
    ('body', 'match'),
    [
        ('[tools]\nroot = "C:/x"\n', 'unknown key'),
        ('[tools]\ninstall_root = "relative"\n', 'absolute'),
        ('[tools]\nmin_free_gb = "five"\n', 'number'),
    ],
)
def test_a_malformed_tools_table_is_refused_by_name(body: str, match: str) -> None:
    _declare(body)
    with pytest.raises(ValueError, match=match):
        home.tools_table()
