"""HPC-ACCESS-BY-ROLE and quota protection: a machine uses only the accounts its roles reach; a billed one is charged.

Every refusal here has its planted control beside it: the same declaration, one fact changed, allowed.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from typing import Any

import pytest

from lab_commons.hpc import budget as budgetdoor
from lab_commons.hpc.budget import OverBudget, ask_of, charge, spent_and_committed
from lab_commons.hpc.grants import ACCESS_RULE, AccessRefused, Budget, Grant, check_target, load_grants
from lab_commons.hpc.plan import Plan

_VERIFIER = 'lc_test_session_verifier'
_WINDOW = {'core_hours': 100, 'since': '2026-01-01', 'until': '2028-01-01'}


def _declared(reach: list[str], **extra: object) -> dict[str, Any]:
    """One free cluster, one billed cluster; a research and a verification account on the free one."""
    return {
        'reach': reach,
        'session_reach': {'lead': ['verification']},
        'session_verifier': f'{_VERIFIER}:roles',
        'clusters': {
            'free': {
                'hosts': ['f1', 'f2'],
                'slurm_accounts': {'acct-a': {'billed': False}, 'acct-b': {'billed': False}},
            },
            'paid': {
                'hosts': ['p1'],
                'root': '/work/proj/{user}',
                'slurm_accounts': {
                    'proj': {
                        'billed': True,
                        'budget': {'core_hours': 100, 'since': '2026-01-01', 'until': '2028-01-01'},
                    }
                },
            },
        },
        'grant': [
            {'user': 'res', 'cluster': 'free', 'slurm_account': 'acct-a', 'role': 'research', 'owner': 'A'},
            {'user': 'ver', 'cluster': 'free', 'slurm_account': 'acct-b', 'role': 'verification'},
            {'user': 'pay', 'cluster': 'paid', 'slurm_account': 'proj', 'role': 'research'},
        ],
        **extra,
    }


@pytest.fixture
def session_roles(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """The roles the declared verifier proves this session holds -- empty unless a test adds one."""
    held: set[str] = set()
    module = types.ModuleType(_VERIFIER)
    module.roles = lambda: frozenset(held)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _VERIFIER, module)
    return held


def test_a_machine_reaching_nothing_is_refused_every_account(session_roles: set[str]) -> None:
    with pytest.raises(AccessRefused, match=ACCESS_RULE):
        load_grants(_declared([]))
    # A session role never widens a machine that reaches nothing.
    session_roles.add('lead')
    with pytest.raises(AccessRefused, match=ACCESS_RULE):
        load_grants(_declared([]))
    # Planted control: the same declaration with a reach loads.
    assert load_grants(_declared(['research'])).grants


@pytest.mark.usefixtures('session_roles')
def test_a_research_machine_is_refused_a_verification_account_by_rule_and_remedy() -> None:
    machine = load_grants(_declared(['research']))
    assert [g.user for g in machine.grants] == ['res', 'pay']
    with pytest.raises(AccessRefused, match=rf"'verification' account.*{ACCESS_RULE}.*lead session role"):
        machine.grant('ver')
    assert machine.grant('res').owner == 'A', 'planted control: a reached account is handed out'
    with pytest.raises(LookupError, match=r'no \[\[hpc\.grant\]\] declares'):
        machine.grant('nobody')


def test_a_verified_session_role_adds_verification_on_a_research_machine(session_roles: set[str]) -> None:
    session_roles.add('lead')
    machine = load_grants(_declared(['research']))
    assert {g.user for g in machine.grants} == {'res', 'ver', 'pay'}
    assert machine.grant('ver').role == 'verification'
    # Planted control: a role the session_reach does not map adds nothing.
    session_roles.clear()
    session_roles.add('someone-else')
    with pytest.raises(AccessRefused):
        load_grants(_declared(['research'])).grant('ver')


@pytest.mark.usefixtures('session_roles')
def test_a_machine_that_reaches_both_uses_both() -> None:
    assert {g.user for g in load_grants(_declared(['research', 'verification'])).grants} == {'res', 'ver', 'pay'}


@pytest.mark.usefixtures('session_roles')
def test_a_session_verifier_that_does_not_load_is_refused_not_ignored() -> None:
    with pytest.raises(ValueError, match='session_verifier'):
        load_grants(_declared(['research'], session_verifier='lc_no_such_module:roles'))


@pytest.mark.usefixtures('session_roles')
def test_the_cluster_facts_reach_each_grant_once_declared() -> None:
    machine = load_grants(_declared(['research']))
    paid = machine.grant('pay')
    assert paid.hosts == ('p1',)
    assert paid.root == '/work/proj/pay', "a root override is the cluster's, the user filled in"
    assert paid.billed
    assert paid.budget == Budget(core_hours=100, since='2026-01-01', until='2028-01-01')
    assert not machine.grant('res').billed
    assert machine.grant('res').root == '', 'planted control: no override, trees live under home'


@pytest.mark.usefixtures('session_roles')
def test_an_at_target_that_is_an_unreached_declared_account_is_refused() -> None:
    with pytest.raises(AccessRefused, match=ACCESS_RULE):
        check_target('ver@f2', _declared(['research']))
    check_target('res@f1', _declared(['research']))  # planted control: reached
    check_target('stranger@elsewhere', _declared(['research']))  # not a declared account: not this rule's
    check_target('ver@f1', {})  # no [hpc] table (the account's own login node): not this rule's


# -- quota protection --------------------------------------------------------------------------------------------

_PLAN = Plan(
    partitions=('std',), qos='', account='proj', cpus=4, mem_mb=1024, gpus=0, minutes=60,
    shards=((0, 1), (1, 2)), throttle=2, free_slots=2, makespan_minutes=60.0,
)  # fmt: skip
_PAID = Grant(
    user='pay', hosts=('p1',), slurm_account='proj', billed=True, budget=Budget(100, '2026-01-01', '2028-01-01')
)
_FREE = Grant(user='res', hosts=('f1',), slurm_account='acct-a')


def _ledger(text: str) -> tuple[list[str], Any]:
    asked: list[str] = []

    def run(command: str, _stdin: str | None) -> str:
        asked.append(command)
        return text

    return asked, run


def test_the_ledger_counts_finished_spend_and_open_jobs_whole() -> None:
    out = 'COMPLETED|4|60|36000\nFAILED|2|30|3600\nRUNNING|8|120|7200\nPENDING|2|60|0\nshort|line\n'
    spent, committed = spent_and_committed(out)
    assert spent == pytest.approx(11.0, abs=1e-9)
    assert committed == pytest.approx(8 * 2 + 2 * 1, abs=1e-9), 'an open job counts its whole request'
    assert ask_of(_PLAN) == pytest.approx(8.0, abs=1e-9), '2 tasks x 4 CPUs x 1 h'


def test_a_billed_submission_over_budget_is_refused_before_sbatch() -> None:
    asked, run = _ledger('COMPLETED|4|60|334800\n')  # 93 core-h spent: 7 left, the ask is 8
    with pytest.raises(OverBudget, match=r'asks 8\.0 core-h.*7\.0 left'):
        charge(run, _PAID, ask_of(_PLAN), today=_INSIDE)
    assert asked == [budgetdoor.ledger_command(_PAID, today=_INSIDE)]
    assert '-A proj' in asked[0]
    assert '-S 2026-01-01' in asked[0]
    assert ' -a ' in asked[0], "the budget is the account's: every user is counted"
    # Planted control: one core-hour less spent and it fits.
    _, run = _ledger('COMPLETED|4|60|331200\n')
    charge(run, _PAID, ask_of(_PLAN), today=_INSIDE)


def test_a_billed_account_without_a_budget_is_refused() -> None:
    _, run = _ledger('')
    with pytest.raises(OverBudget, match='declares no budget'):
        charge(run, Grant(user='pay', hosts=('p1',), slurm_account='proj', billed=True), 1.0)


def test_a_free_partition_is_never_budget_checked() -> None:
    asked, run = _ledger('COMPLETED|4|60|999999999\n')
    charge(run, _FREE, 10_000.0)
    assert asked == [], 'a free account is never read'


# -- a budget has an end: [since, until) -------------------------------------------------------------------------

_INSIDE = date(2027, 6, 1)


def test_a_budget_reads_toml_dates_and_refuses_an_empty_window() -> None:
    assert Budget(1, date(2026, 9, 1), date(2028, 1, 1)) == Budget(1, '2026-09-01', '2028-01-01')
    with pytest.raises(ValueError, match='until'):
        Budget(1, '2026-09-01', '2026-09-01')
    with pytest.raises(TypeError):
        Budget(1, '2026-09-01')  # type: ignore[call-arg]  -- an allocation always has an end


def test_a_submission_before_the_budget_begins_is_refused_unread() -> None:
    asked, run = _ledger('')
    with pytest.raises(OverBudget, match='not begun'):
        charge(run, _PAID, 1.0, today=date(2025, 12, 31))
    assert asked == []


def test_a_submission_at_or_after_until_is_refused_unread() -> None:
    asked, run = _ledger('')
    for day in (date(2028, 1, 1), date(2028, 6, 1)):
        with pytest.raises(OverBudget, match='has ended'):
            charge(run, _PAID, 1.0, today=day)
    assert asked == []
    charge(run, _PAID, 1.0, today=date(2027, 12, 31))  # planted control: the last day inside is allowed


def test_the_ledger_counts_only_the_window() -> None:
    command = budgetdoor.ledger_command(_PAID, today=_INSIDE)
    assert '-S 2026-01-01T00:00:00' in command
    assert '-E 2027-06-01T23:59:59' in command, 'inside the window it ends today'
    late = Grant(
        user='pay', hosts=('p1',), slurm_account='proj', billed=True, budget=Budget(100, '2026-01-01', '2027-01-01')
    )
    assert '-E 2026-12-31T23:59:59' in budgetdoor.ledger_command(late, today=date(2026, 12, 31)), 'never past until'
