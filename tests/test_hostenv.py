"""THE HOST CONTEXT: lab-commons' one ENV-IS-READ-ONCE entry, and the machine home it is passed down to.

The machine-home override stays (class B): tests relocate the home for themselves AND for the processes they spawn,
which only the environment reaches. It is read in :mod:`lab_commons.hostenv` alone and passed to
:func:`lab_commons.home.home` as a typed record; the old constant on the home module is refused by name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import lab_commons.home as home_module
from lab_commons import hostenv
from lab_commons.home import git_credential_stores, home
from lab_commons.hostenv import HOME_ENV, XDG_CONFIG_ENV, Host
from lab_commons.hpc.retired import RETIRED


def test_the_context_types_the_host_facts_read_by_name() -> None:
    env = {HOME_ENV: '/m', XDG_CONFIG_ENV: '/x', 'UNRELATED': '1'}
    assert hostenv.context(env) == Host(machine_home='/m', xdg_config_home='/x')
    assert hostenv.context({}) == Host()


def test_the_context_reads_this_process_when_given_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_ENV, '/relocated')
    monkeypatch.delenv(XDG_CONFIG_ENV, raising=False)
    assert hostenv.context() == Host(machine_home='/relocated')


def test_a_record_passed_down_decides_the_home_over_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED CONTROL: the process says one home, the record another; the record wins, both ways."""
    monkeypatch.setenv(HOME_ENV, str(tmp_path / 'process'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path / 'user'))
    assert home() == tmp_path / 'process'
    assert home(Host(machine_home=str(tmp_path / 'record'))) == tmp_path / 'record'
    assert home(Host()) == tmp_path / 'user' / '.motronics', 'an empty record is the default layout, env unread'


def test_the_credential_store_follows_the_xdg_fact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(XDG_CONFIG_ENV, str(tmp_path / 'xdg'))
    assert git_credential_stores()[1] == tmp_path / 'xdg' / 'git' / 'credentials'


def test_the_home_module_reads_no_environment_and_its_old_constant_is_refused_by_name() -> None:
    """The entry is the only reader; the constant moved with the read, and the old spelling is registered."""
    tree = ast.parse(Path(home_module.__file__).read_text(encoding='utf-8'))
    reads = [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in {'environ', 'getenv'}
    ]
    assert reads == []
    assert not hasattr(home_module, 'HOME_ENV')
    moved = {spelling for spelling, door in RETIRED.items() if 'lab_commons.hostenv import HOME_ENV' in door}
    moved |= {spelling for spelling, door in RETIRED.items() if door.startswith('lab_commons.hostenv.HOME_ENV')}
    assert len(moved) == len(('the attribute', 'the import')), 'each old spelling is refused by name'
