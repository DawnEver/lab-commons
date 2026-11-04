"""HPC account affinity, RESEARCH side (user ruling 2026-10-30): a job-file submit never lands on a verify account.

The verify accounts come from the forge's host cards through the installed reader
(:data:`lab_commons.hpc.affinity.READER`); none installed leaves placement exactly as it was.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lab_commons.hpc import __main__ as cli
from lab_commons.hpc import affinity, allocation, jobs
from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Affinity, Grant, Machine
from lab_commons.hpc.plan import Unplaceable
from lab_commons.hpc.slurm import parse_snapshot
from lab_commons.hpc.span import Placement

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n'
VERIFY = Grant(user='verifier', hosts=('ada',), slurm_account='acct-free', priority=10)
RESEARCH = Grant(user='researcher', hosts=('ada',), slurm_account='acct-free')
SMALL = Cost(cpus=1, mem_gb=2, seconds=600)


def _snap(cpus_held: float = 0) -> object:
    rows = f'acct-free|{cpus_held:g}|1|{cpus_held:g}G|N/A\n' if cpus_held else ''
    return parse_snapshot(f'{IDLE}{rows}@@@ shared\n@@@ pending\n', 'acct-free')


def _machine(held: Affinity | None) -> Machine:
    return Machine(session=None, grants=(VERIFY, RESEARCH), affinity=held)


def _plan(monkeypatch: pytest.MonkeyPatch, machine: Machine, research_held: float = 40) -> tuple[Placement, ...]:
    """Where 4 small items go: the verify account EMPTIER and first in the file, the research account loaded."""
    candidates = [(VERIFY, _snap()), (RESEARCH, _snap(research_held))]
    monkeypatch.setattr(jobs, '_answering', lambda grants, _reach: [c for c in candidates if c[0] in grants])
    config = Config(job=JobSpec(name='r', workdir='~/w', entry='m:f'), cost=SMALL, policy=Policy())
    return jobs.plan_job(config, 4, machine, reach=lambda _g: None)


def test_with_no_affinity_declared_placement_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PLANTED CONTROL of every test below: unaffined, the emptier verify account takes the work."""
    placed = _plan(monkeypatch, _machine(None))
    assert [p.grant for p in placed] == [VERIFY]


def test_research_picks_a_research_account_even_when_a_verify_account_is_emptier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    placed = _plan(monkeypatch, _machine(Affinity(verify=frozenset({'verifier'}))))
    assert [p.grant for p in placed] == [RESEARCH]


def test_research_never_lands_on_an_idle_verify_account_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """User ruling 2026-10-30: the research account is full (96 of 96 CPUs held), the verify account idle -- WAIT."""
    placed = _plan(monkeypatch, _machine(Affinity(verify=frozenset({'verifier'}))), research_held=96)
    assert [(p.grant, p.plan.room) for p in placed] == [(RESEARCH, 0)]
    alone = Machine(session=None, grants=(VERIFY,), affinity=Affinity(verify=frozenset({'verifier'})))
    with pytest.raises(Unplaceable, match=r'never take research unless the user opts in'):
        _plan(monkeypatch, alone)


def test_the_users_opt_in_lets_research_borrow_a_verify_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """PLANTED CONTROL of the test above: with the opt-in the same full research account is passed over."""
    placed = _plan(monkeypatch, _machine(Affinity(verify=frozenset({'verifier'}), borrow=True)), research_held=96)
    assert [p.grant for p in placed] == [VERIFY]


def _record(*, hours: float, borrow: tuple[str, ...]) -> Callable[[], bytes]:
    expires = datetime.now(UTC) + timedelta(hours=hours)
    return lambda: allocation.render(allocation.Allocation(expires, {}, frozenset(borrow))).encode('utf-8')


@pytest.mark.parametrize(
    ('hours', 'borrow', 'verdict'),
    [
        (1, (allocation.RESEARCH_BORROWS_VERIFY,), 'borrows'),
        (-1, (allocation.RESEARCH_BORROWS_VERIFY,), 'stays'),
        (1, (allocation.VERIFY_BORROWS_RESEARCH,), 'stays'),
        (1, (), 'stays'),
    ],
    ids=['unexpired-opt-in-borrows', 'expired-opt-in-is-ignored', 'the-other-direction-is-not-this-one', 'none'],
)
def test_resolve_borrows_only_by_an_unexpired_research_opt_in(
    hours: float, borrow: tuple[str, ...], verdict: str
) -> None:
    held = Affinity(verify=frozenset({'verifier'}))
    resolved = affinity.resolve(_machine(None), lambda: held, _record(hours=hours, borrow=borrow))
    assert resolved.affinity == Affinity(verify=held.verify, borrow=verdict == 'borrows')
    assert affinity.resolve(_machine(None), lambda: held, lambda: None).affinity == held, 'no record borrows nothing'


def test_a_record_naming_an_unknown_direction_is_refused_by_name() -> None:
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match=r'research->box'):
        allocation.parse(f'{{"expires": "{expires}", "borrow": ["research->box"]}}')
    assert allocation.parse(f'{{"expires": "{expires}"}}').borrow == frozenset(), 'PLANTED CONTROL: none is fine'


def test_resolve_reads_the_installed_reader_and_a_failing_or_absent_one_leaves_no_affinity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = Affinity(verify=frozenset({'verifier'}))

    class _Point:
        def load(self) -> Callable[[], Affinity]:
            return lambda: held

    monkeypatch.setattr(affinity, 'entry_points', lambda group: [_Point()] if group == affinity.READER else [])
    assert affinity.resolve(_machine(None)).affinity == held

    def broken() -> Affinity:
        raise OSError

    assert affinity.resolve(_machine(None), broken).affinity is None
    monkeypatch.setattr(affinity, 'entry_points', lambda **_k: [])
    assert affinity.resolve(_machine(None)).affinity is None


@pytest.mark.parametrize('verb', ['plan', 'submit', 'retry'])
def test_a_job_file_submitter_reads_the_affinity_and_a_forge_run_does_not(
    monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    held = Affinity(verify=frozenset({'verifier'}))
    seen: list[Machine] = []
    monkeypatch.setattr(cli, 'load_grants', lambda: _machine(None))
    monkeypatch.setattr(cli.allocation, 'resolve', lambda machine: machine)
    monkeypatch.setattr(affinity, 'declared', lambda: held)
    monkeypatch.setattr(cli, '_job_config', lambda _p, _a: cli.Config())
    monkeypatch.setattr(cli, '_jobs', lambda _verb, _config, machine, *_a, **_k: seen.append(machine) or 0)
    monkeypatch.setattr(cli, '_run_submit', lambda _args, machine, _config: seen.append(machine) or 0)
    cli.main([verb, '-c', 'job.toml'])
    cli.main(['run', 'submit', '--sha', 'a' * 40, '--repo-url', 'https://x/y', '--scope', 'full'])
    assert [m.affinity for m in seen] == [held, None]
