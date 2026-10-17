"""Paged placement over the one account pool -- page-sized tasks, a rotating large-job region that drains.

Every snapshot is the recorded cluster's (``acct-free``: ``GrpTRES cpu=96,mem=360G``, Ada's shape) with its
running jobs and pending tasks written in, so group-memory headroom is read exactly as a live probe reads it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lab_commons.hpc import jobs
from lab_commons.hpc.config import Cost, Policy, load_config
from lab_commons.hpc.grants import Grant, Machine, Paging, load_grants
from lab_commons.hpc.jobs import plan_job
from lab_commons.hpc.plan import allocate, region
from lab_commons.hpc.slurm import parse_snapshot
from lab_commons.hpc.span import span

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n'
PAGING = Paging(page_cpus=8, page_mem_gb=20, large_pages=4, movable_minutes=30)
POOL = tuple(Grant(user='me', hosts=('ada',), slurm_account=f'acct-{i}') for i in range(5))
SMALL = Cost(cpus=8, mem_gb=16, seconds=600)
LARGE = Cost(cpus=8, mem_gb=300, seconds=3600)


def _snap(running: list[tuple[float, float]], pending: list[tuple[float, float]] = ()) -> object:
    """*running* and *pending* are ``(cpus, mem_gb)`` tasks on one account."""
    rows = ''.join(f'acct-free|{c:g}|1|{m:g}G|N/A\n' for c, m in running)
    waits = ''.join(f'{c:g}|{m:g}G|1\n' for c, m in pending)
    return parse_snapshot(f'{IDLE}{rows}@@@ shared\n@@@ pending\n{waits}', 'acct-free')


def _pool(used_gb: list[float], pending: dict[int, list[tuple[float, float]]] | None = None) -> list:
    pending = pending or {}
    return [
        (g, _snap([(8, used)] if used else [], pending.get(i, [])))
        for i, (g, used) in enumerate(zip(POOL, used_gb, strict=True))
    ]


def _live(running: dict[int, list[tuple[float, float, float]]], waits: dict[int, list[tuple[float, float]]]) -> list:
    """The pool as probed now: each account's running ``(cpus, mem_gb, ends)`` tasks and its waiting ones."""
    return [(g, _snap([(c, m) for c, m, _ in running[i]], waits.get(i, []))) for i, g in enumerate(POOL)]


def test_a_task_is_sized_in_pages() -> None:
    assert PAGING.size(SMALL) == 'small'
    assert PAGING.size(Cost(cpus=8, mem_gb=20)) == 'small', 'exactly one page'
    assert PAGING.size(Cost(cpus=9, mem_gb=1)) == 'medium', 'a CPU past the page'
    assert PAGING.size(Cost(cpus=8, mem_gb=80)) == 'medium', 'four pages is not large'
    assert PAGING.size(Cost(cpus=8, mem_gb=81)) == 'large'
    assert PAGING.size(Cost(cpus=40, mem_gb=1)) == 'large'


def test_paging_is_declared_in_the_hpc_table() -> None:
    grant = {'user': 'me', 'hosts': ['ada'], 'slurm_account': 'a'}
    machine = load_grants(
        {'grant': [grant], 'paging': {'page_cpus': 8, 'page_mem_gb': 20, 'large_pages': 4, 'movable_minutes': 30}}
    )
    assert machine.paging == PAGING
    assert load_grants({'grant': [grant]}).paging is None, 'undeclared: no region, placement as before'
    with pytest.raises(ValueError, match='paging'):
        load_grants({'grant': [grant], 'paging': {'page_cpus': 8}})


def test_the_region_is_the_emptiest_account_and_it_rotates() -> None:
    assert region([s for _, s in _pool([100, 50, 200, 300, 60])], PAGING) == 1
    assert region([s for _, s in _pool([100, 80, 200, 0, 60])], PAGING) == 3
    # A waiting large job pins the region where it waits, even once another account is emptier.
    pinned = _pool([100, 80, 200, 0, 60], {2: [(8, 300)]})
    assert region([s for _, s in pinned], PAGING) == 2
    # Planted control: a waiting SMALL task pins nothing.
    assert region([s for _, s in _pool([100, 80, 200, 0, 60], {2: [(8, 16)]})], PAGING) == 3


def test_small_tasks_best_fit_onto_the_fullest_account_with_room() -> None:
    pool = _pool([100, 300, 340, 50, 200])
    grant, _ = allocate(1, SMALL, pool, session=None, policy=Policy(), paging=PAGING, rerun_safe=True)
    assert grant is POOL[2], '340 GB held leaves 20 GB: the fullest account that still holds 16 GB'
    # Planted control: without paging the file order breaks the tie, fit unread.
    grant, _ = allocate(1, SMALL, pool, session=None, policy=Policy())
    assert grant is POOL[0]


def test_a_non_movable_task_never_lands_in_the_region() -> None:
    pool = _pool([350, 350, 350, 350, 0])  # only the region has room
    grant, plan = allocate(1, SMALL, pool, session=None, policy=Policy(), paging=PAGING, rerun_safe=False)
    assert grant is not POOL[4]
    assert plan.room == 0, 'it waits on a full account rather than borrow the region'
    long = Cost(cpus=8, mem_gb=16, seconds=3600)
    grant, _ = allocate(1, long, pool, session=None, policy=Policy(), paging=PAGING, rerun_safe=True)
    assert grant is not POOL[4], 'rerun-safe but past the movable wall'
    medium = Cost(cpus=16, mem_gb=40, seconds=600)
    grant, _ = allocate(1, medium, pool, session=None, policy=Policy(), paging=PAGING, rerun_safe=True)
    assert grant is not POOL[4], 'only SMALL movable tasks borrow'
    # Planted control: a small movable task may borrow it.
    grant, _ = allocate(1, SMALL, pool, session=None, policy=Policy(), paging=PAGING, rerun_safe=True)
    assert grant is POOL[4]


def test_a_large_job_goes_to_the_region_and_never_spreads() -> None:
    pool = _pool([100, 50, 200, 300, 60])
    grant, _ = allocate(1, LARGE, pool, session=None, policy=Policy(), paging=PAGING)
    assert grant is POOL[1]
    placements = span(3, LARGE, pool, session=None, policy=Policy(), paging=PAGING)
    assert [p.grant for p in placements] == [POOL[1]]


def test_fragmented_memory_drains_one_region_for_a_large_job_within_one_movable_wall() -> None:
    """72 GB free on each of 5 accounts: a 300 GB job waits on the region, which drains while small tasks flow."""
    # Each account holds eight 36 GB movable tasks (288 of 360 GB), ending staggered over the next 30 min.
    running = {i: [(8.0, 36.0, 5.0 * (k % 6 + 1)) for k in range(8)] for i in range(5)}
    pending_large: int | None = None
    started_at: float | None = None
    elsewhere = 0
    for minute in range(0, 61, 5):
        for tasks in running.values():
            tasks[:] = [t for t in tasks if t[2] > minute]
        waits = (
            {pending_large: [(LARGE.cpus, LARGE.mem_gb)]} if pending_large is not None and started_at is None else {}
        )
        if pending_large is None:
            grant, _ = allocate(1, LARGE, _live(running, waits), session=None, policy=Policy(), paging=PAGING)
            pending_large = POOL.index(grant)
            continue
        if started_at is None:
            free = 360.0 - sum(m for _, m, _ in running[pending_large])
            if free >= LARGE.mem_gb:  # Slurm starts it the moment the group memory allows
                started_at = minute
                running[pending_large].append((LARGE.cpus, LARGE.mem_gb, 10_000.0))
                waits = {}
        for _ in range(4):  # small movable tasks keep arriving
            grant, plan = allocate(
                1, SMALL, _live(running, waits), session=None, policy=Policy(), paging=PAGING, rerun_safe=True
            )
            if not plan.room:
                break
            i = POOL.index(grant)
            if started_at is None:
                assert i != pending_large, f'minute {minute}: a small task was put into the draining region'
            elsewhere += i != pending_large
            running[i].append((SMALL.cpus, SMALL.mem_gb, minute + 15.0))
    assert started_at is not None
    assert started_at <= PAGING.movable_minutes, f'the region drained in {started_at} min'
    assert elsewhere > 0, 'small tasks kept flowing on the other accounts'


def test_a_job_declares_itself_rerun_safe_and_its_placement_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'job.toml'
    path.write_text('[job]\nrerun_safe = true\n[cost]\ncpus = 8\nmem_gb = 16\nseconds = 600\n', encoding='utf-8')
    config = load_config(path)
    assert config.job.rerun_safe
    pool = {g.slurm_account: snapshot for g, snapshot in _pool([350, 350, 350, 350, 0])}  # only the region has room
    monkeypatch.setattr(jobs, 'probe', lambda _runner, account: pool[account])
    machine = Machine(session=None, grants=POOL, paging=PAGING)
    assert plan_job(config, 1, machine, reach=lambda _g: None)[0].grant is POOL[4]
    # Planted control: the same job undeclared never borrows the region.
    undeclared = replace(config, job=replace(config.job, rerun_safe=False))
    assert plan_job(undeclared, 1, machine, reach=lambda _g: None)[0].grant is not POOL[4]
