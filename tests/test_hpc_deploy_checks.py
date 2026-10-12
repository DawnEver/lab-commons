"""The deploy's account checks: the credential store, the tools on the tick PATH, and the grant."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc import deploy_checks
from lab_commons.hpc.deploy_spec import Deployment, Repo

URL: Final = 'https://forge.example.org/o/r.git'


@pytest.mark.parametrize(
    ('helper', 'exists', 'mode', 'fix'),
    [
        ('', True, 0o600, 'git config --global credential.helper store'),
        ('store', False, 0o600, 'is missing'),
        ('store', True, 0o640, 'chmod 600'),
        ('store', True, 0o600, ''),
        ('manager', False, 0o644, ''),  # another helper keeps its secret elsewhere
    ],
)
def test_the_credential_store_must_be_configured_and_private(
    tmp_path: Path, helper: str, *, exists: bool, mode: int, fix: str
) -> None:
    store = tmp_path / '.git-credentials'
    if exists:
        store.write_text('x', encoding='utf-8')
    problem = deploy_checks.store_problem(helper, store, mode=mode if exists else None)
    assert (fix in problem) if fix else problem == ''


def test_uv_is_always_needed_and_each_tool_is_installed_or_refused() -> None:
    """PLANTED CONTROL: a box that has everything installs nothing and refuses nothing."""
    installs, refused = deploy_checks.tool_plan(['cargo', 'cmake', 'cc'], found=lambda _t: False)
    assert [tool for tool, _script in installs] == ['uv', 'cargo', 'cmake'], 'uv first: cmake comes through it'
    assert refused == ['cc']
    assert deploy_checks.tool_plan(['cargo', 'cc'], found=lambda _t: True) == ([], [])


SPEC: Final = Deployment('login02', 'src', '3.13', (), (Repo('lab-commons', URL, 'trunk'),), '', ('shortq', 'defq'))


def test_the_grant_comes_from_slurm_and_the_declaration() -> None:
    grant = deploy_checks.grant_of('alice', 'acct-free|cpu=96,mem=360G\nacct-free|cpu=96\n', spec=SPEC, host='h.x')
    assert grant == {
        'user': 'alice',
        'hosts': ['h.x'],
        'slurm_account': 'acct-free',
        'cpus': 96,
        'partitions': ['shortq', 'defq'],
    }


@pytest.mark.parametrize(
    ('sacct', 'spec', 'named'),
    [
        ('a|cpu=1\nb|cpu=2\n', SPEC, '--slurm-account'),
        ('', SPEC, 'no Slurm association'),
        ('a|mem=1G\n', SPEC, 'cpu='),
        ('a|cpu=1\n', Deployment('h', 'src', '3.13', (), (), '', ()), '--partition'),
        ('a|cpu=1\n', Deployment('h', 'src', '3.13', (), (), 'zz', ('q',)), 'zz'),
    ],
)
def test_a_grant_that_cannot_be_derived_is_refused_naming_why(sacct: str, spec: Deployment, named: str) -> None:
    refused = deploy_checks.grant_of('alice', sacct, spec=spec, host='h')
    assert isinstance(refused, str)
    assert named in refused
