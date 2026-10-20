"""Gathering a remote run (:mod:`lab_commons.hpc.run`): ONE short call per tick folds a finished round.

Still active is pending -- and each live band's throttle follows the headroom (:func:`rethrottled`); a
finished round is folded, the memory of its KILLED items' files grows (:func:`grown_after`), and what it left
unfinished is re-submitted as the next round (:func:`lab_commons.hpc.run.submit_round`), or the record is
written. Nothing resident waits: the retries are driven by gather calls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from lab_commons.compute import learned
from lab_commons.compute.learned import OOM_GROWTH, Measured
from lab_commons.hpc.cluster import Runner, probe, throttle
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.needsets import needs_of
from lab_commons.hpc.phases import round_clock
from lab_commons.hpc.plan import live_throttle
from lab_commons.hpc.records import files_made, fold, job_measured, read_streams, record_of, stream_sources
from lab_commons.hpc.run import ACTIVE, RETRIES, load_state, save_state, shares_home, submit_round
from lab_commons.hpc.sacct import shard_clock
from lab_commons.hpc.slurm import Snapshot
from lab_commons.hpc.testcells import TestCell
from lab_commons.outcomes import Cell, Reason, Status

__all__ = ['gather_run', 'grown_after', 'rethrottled']


def gather_run(
    sha: str,
    part: str,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
) -> dict[str, Any] | None:
    """ONE short call: the record when the run is done, else ``None`` (pending).

    A finished round is folded; what a killed shard left unfinished is re-submitted (up to
    :data:`~lab_commons.hpc.run.RETRIES` more rounds) and the call returns pending -- THE RETRIES ARE DRIVEN
    BY GATHER CALLS, nothing resident waits. An outage raises :class:`~lab_commons.hpc.cluster.Unreachable`
    and loses nothing: the jobs keep running and the state stays on the cluster, so the next gather
    picks up where this one could not.
    """
    runners = {g.account: connect(g) for g in machine.grants}
    state, home = load_state(sha, part, machine, runners)
    if 'record' in state:
        return state['record']
    current = state['current']
    account = tuple(current['account'])
    run = runners[account]
    clocks = shard_clock(run, current['job_ids'])
    states = {shard: clock.state for shard, clock in clocks.items()}
    if len(states) < current['shards'] or any(s in ACTIVE for s in states.values()):
        grant = next(g for g in machine.grants if g.account == account)
        snapshot, qos = probe(run, grant.slurm_account), state['rounds'][-1]['qos']
        if rethrottled(current, states, run, snapshot, cost=cost, qos=qos):
            save_state(home, state)
        return None
    measured = Measured.of(state['measured'])
    streams = read_streams(run, *stream_sources(sha, current['tag']))
    planned = state['rounds'][-1]
    band_of = {n: band for band in current['bands'] for n in range(*band['shards'])}
    walls = [
        clocks[n].elapsed if n in clocks and clocks[n].elapsed > 0 else band_of[n]['minutes'] * 60
        for n in range(len(planned['shards']))
    ]
    shard_of = {i: n for n, (a, b) in enumerate(planned['shards']) for i in range(a, b)}
    oomed = [i for i, n in shard_of.items() if states.get(n) == 'OUT_OF_MEMORY']
    pending, killed, busy = fold(
        current['items'],
        streams,
        state['cells'],
        measured,
        shards=planned['shards'],
        walls=walls,
        alone={i: band_of[shard_of[i]]['mem_mb'] for i in oomed if band_of[shard_of[i]]['at_ceiling']},
    )
    planned['clock'] = round_clock(clocks, busy)
    planned['walls_s'] = walls
    state['measured'] = measured.record()
    learned.learn({'repo': state['repo'], **state['measured']})
    timed_out = 'TIMEOUT' in states.values()
    oom = 'OUT_OF_MEMORY' in states.values() or (killed and not timed_out)
    state['grown'] = grown_after(current['items'], state['cells'], shard_of, states, state.get('grown', {}), oom=oom)
    if timed_out:
        state['min_minutes'] = 2 * planned['minutes']
    if pending and (len(state['rounds']) <= RETRIES or (oom and current['ceiling'])):
        grant = next(g for g in machine.grants if g.account == account)
        snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants if shares_home(g, grant)]
        try:
            submit_round(
                state, pending, runners=runners, snapshots=snapshots, machine=machine, cost=cost, policy=policy
            )
        except ValueError as refused:  # no grant can take it, now or ever: a definite red, never a hang
            unplaced = Cell(Status.UNAVAILABLE, reason=Reason('placement', f'could not be placed: {refused}'))
            state['cells'].update({node: TestCell(unplaced).to_plain() for group in pending for node in group})
            pending = []
        else:
            save_state(home, state)
            return None
    lost = Cell(Status.INFRA, reason=Reason('lost', f'still unfinished after {len(state["rounds"])} round(s)'))
    state['cells'].update({node: TestCell(lost).to_plain() for group in pending for node in group})
    record = record_of(state, next(g.slurm_account for g in machine.grants if g.account == account))
    state['record'] = record
    save_state(home, state)
    learned.record_run(
        state['repo'],
        learned.SLURM,
        needs_of(state['platform']),
        job_measured(state['rounds'], state['cells'], measured, cpus=cost.cpus, files=files_made(home, state)),
    )
    return record


def rethrottled(
    current: dict[str, Any], states: Mapping[int, str], run: Runner, snapshot: Snapshot, *, cost: Cost, qos: str
) -> bool:
    """Re-set each live band's array throttle to the headroom now (:func:`live_throttle`); whether any moved.

    Bands are taken heaviest first, each one's added tasks charged to the account before the next is sized,
    so two bands never both spend the same free memory.
    """
    moved = False
    for band in current['bands']:
        shards = range(*band['shards'])
        unfinished = sum(1 for n in shards if states.get(n, 'PENDING') in ACTIVE)
        if not unfinished:
            continue
        running = sum(1 for n in shards if states.get(n) == 'RUNNING')
        task = replace(cost, mem_gb=band['mem_mb'] / 1024)
        limit = snapshot.quota.qos_max_jobs.get(qos)
        tasks = live_throttle(snapshot, task, running=running, unfinished=unfinished, max_jobs=limit)
        extra = max(0, tasks - running)
        used = dict(snapshot.quota.used)
        used['cpu'] = used.get('cpu', 0.0) + extra * cost.cpus
        used['mem'] = used.get('mem', 0.0) + extra * band['mem_mb']
        snapshot = replace(snapshot, quota=replace(snapshot.quota, used=used))
        if tasks != band['throttle']:
            throttle(run, band['job_id'], tasks)
            band['throttle'] = tasks
            moved = True
    return moved


def grown_after(
    items: list[dict[str, Any]],
    cells: Mapping[str, Any],
    shard_of: Mapping[int, int],
    states: Mapping[int, str],
    grown: Mapping[str, float],
    *,
    oom: bool,
) -> dict[str, float]:
    """The run's memory growth per FILE after a round: a KILLED item's files grow, a finished one's reset.

    In a round that ran out of memory, an item left unfinished in a shard that did not time out was killed,
    and each of its files asks :data:`~lab_commons.compute.learned.OOM_GROWTH` times more next round, never
    every later item (Ada 2026-10-19: one heavy file's kill doubled every task of the part). A file whose
    item finished every id outside an OUT_OF_MEMORY shard is back at its measured peak (:meth:`Measured.learn`
    keeps it): its growth is dropped.
    """
    after = dict(grown)
    killed: set[str] = set()  # a file grows ONCE a round, however many of its items were killed
    for index, item in enumerate(items):
        files = {node.split('::', maxsplit=1)[0] for node in item['ids']}
        shard = states.get(shard_of[index], '')
        if all(node in cells for node in item['ids']) and shard != 'OUT_OF_MEMORY':
            for name in files:
                after.pop(name, None)
        elif oom and shard != 'TIMEOUT':
            killed |= files
    for name in killed:
        after[name] = after.get(name, 1.0) * OOM_GROWTH
    return after
