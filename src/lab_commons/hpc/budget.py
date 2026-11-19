"""QUOTA PROTECTION FOR A BILLED ACCOUNT: every ``sbatch`` on it is charged against its declared TOTAL budget first.

A billed Slurm account (``[hpc.clusters.<name>.slurm_accounts.<account>] billed = true``) declares
``budget = { core_hours, since, until }`` (:class:`lab_commons.hpc.grants.Budget`). Before any submission on it,
:func:`charge` reads the grant's own login's ledger ONCE from ``sacct`` and refuses (:class:`OverBudget`) unless

    spent + committed + ask <= core_hours

* ``spent``: ``CPUTimeRAW`` of every finished job the grant's own login ran on the account inside
  ``[since, until)`` -- never another user's: on a shared account their spend would refuse every submit of ours;
* ``committed``: a job still pending or running may spend up to its requested CPUs times its time limit -- counted
  WHOLE, so a running job's elapsed part is counted twice. The bound errs toward refusing, never toward overspending;
* ``ask``: this submission's tasks x CPUs x wall.

A billed account with no budget is refused: an undeclared limit is never read as unlimited. A FREE account is
never charged and never read. Nothing here submits; the callers (:mod:`lab_commons.hpc.jobs`,
:mod:`lab_commons.hpc.run`) charge before their ``sbatch``.
"""

from __future__ import annotations

import shlex
from datetime import date, timedelta
from typing import Final

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.grants import Grant
from lab_commons.hpc.plan import Plan

__all__ = ['FIELDS', 'OverBudget', 'ask_of', 'charge', 'ledger_command', 'spent_and_committed']

#: The ``sacct`` columns :func:`spent_and_committed` reads, in its order.
FIELDS: Final = 'State,ReqCPUS,TimelimitRaw,CPUTimeRAW'
#: States of a job that may still spend.
_OPEN: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'RESIZING', 'SUSPENDED', 'CONFIGURING'})
_HOUR: Final = 3600.0


class OverBudget(ValueError):
    """A billed submission would pass its account's declared budget, or the account declares none."""


def ledger_command(grant: Grant, *, today: date) -> str:
    """The ONE ``sacct`` read of *grant*'s login on its Slurm account inside its window, up to *today*: parent rows."""
    assert grant.budget is not None  # noqa: S101 -- charge() refuses a missing budget first
    account = shlex.quote(grant.slurm_account)
    user = shlex.quote(grant.user)
    last = min(today, date.fromisoformat(grant.budget.until) - timedelta(days=1))
    return f'sacct -n -P -X -u {user} -A {account} -S {grant.budget.since}T00:00:00 -E {last}T23:59:59 -o {FIELDS}'


def _number(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0  # 'UNLIMITED', '' -- a time limit Slurm does not state counts as none here


def spent_and_committed(out: str) -> tuple[float, float]:
    """``(spent, committed)`` core-hours from :func:`ledger_command`'s output."""
    spent = committed = 0.0
    for line in out.splitlines():
        fields = line.split('|')
        if len(fields) < len(FIELDS.split(',')):
            continue
        state, cpus, limit_minutes, cpu_seconds = fields[:4]
        if (state.split() or [''])[0] in _OPEN:
            committed += _number(cpus) * _number(limit_minutes) / 60
        else:
            spent += _number(cpu_seconds) / _HOUR
    return spent, committed


def ask_of(plan: Plan, tasks: int | None = None) -> float:
    """Core-hours *plan* may spend: its tasks (default every shard) x CPUs x wall."""
    count = len(plan.shards) if tasks is None else tasks
    return count * plan.cpus * plan.minutes / 60


def charge(run: Runner, grant: Grant, ask_core_hours: float, *, today: date | None = None) -> None:
    """Refuse a submission of *ask_core_hours* on *grant* past its budget; a free grant passes unread.

    Raises:
        OverBudget: the account is billed and declares no budget, *today* (default: the local date) is outside its
            window ``[since, until)``, or the ask does not fit what is left.

    """
    if not grant.billed:
        return
    if grant.budget is None:
        msg = (
            f'{grant.name} is billed and declares no budget: declare [hpc.clusters.{grant.cluster_name or "<name>"}] '
            f'slurm_accounts.{grant.slurm_account} budget = {{ core_hours = <total>, since = <YYYY-MM-DD>, '
            'until = <YYYY-MM-DD> } '
            'before any submission spends it'
        )
        raise OverBudget(msg)
    day = date.today() if today is None else today  # noqa: DTZ011 -- the cluster's calendar day, near enough
    budget = grant.budget
    if day < date.fromisoformat(budget.since):
        msg = f'{grant.name}: its budget has not begun (since {budget.since}) -- refused before sbatch'
        raise OverBudget(msg)
    if day >= date.fromisoformat(budget.until):
        msg = f'{grant.name}: its allocation has ended (until {budget.until}, exclusive) -- refused before sbatch'
        raise OverBudget(msg)
    spent, committed = spent_and_committed(run(ledger_command(grant, today=day), None))
    left = grant.budget.core_hours - spent - committed
    if ask_core_hours > left:
        msg = (
            f'{grant.name}: this submission asks {ask_core_hours:.1f} core-h and the budget of '
            f'{grant.budget.core_hours:g} core-h in [{budget.since}, {budget.until}) has {left:.1f} left '
            f'({spent:.1f} spent, {committed:.1f} committed to queued and running jobs) -- refused before sbatch'
        )
        raise OverBudget(msg)
