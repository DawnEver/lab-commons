"""A job file's ``[policy] grants`` restricts its placement to named grants, refusing a name it cannot honor."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.hpc.config import load_config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.restrict import restricted


def _grant(user: str) -> Grant:
    return Grant(user=user, hosts=('login',), slurm_account='acct', cluster_name='ada', role='research')


_MACHINE = Machine(session=None, grants=(_grant('alice'), _grant('bob')), refused=(_grant('carol'),))


def test_no_named_grants_keeps_the_whole_pool() -> None:
    """PLANTED CONTROL: an undeclared [policy] grants changes nothing."""
    assert restricted(_MACHINE, ()) is _MACHINE


def test_a_job_is_placed_only_on_its_named_grants() -> None:
    assert [one.user for one in restricted(_MACHINE, ('bob',)).grants] == ['bob']
    assert [one.user for one in restricted(_MACHINE, ('ada-alice',)).grants] == ['alice']


def test_a_name_no_grant_declares_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match=r"\['dave'\] name no declared grant"):
        restricted(_MACHINE, ('dave',))


def test_a_grant_this_machine_does_not_reach_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match=r"\['carol'\] are not reached here"):
        restricted(_MACHINE, ('carol',))


def test_the_job_file_declares_grants_under_policy(tmp_path: Path) -> None:
    job = tmp_path / 'job.toml'
    job.write_text('[policy]\ngrants = ["ada-alice"]\n', encoding='utf-8')
    assert load_config(job).policy.grants == ('ada-alice',)
