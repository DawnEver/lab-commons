"""A job file's batch over every grant it spans: submit, status, gather, retry -- one record, many grants.

ONE SUBMISSION, ONE RECORD. ``submit`` spans the items (:func:`lab_commons.hpc.span.span`), makes sure each
chosen grant has the job's tree (*ensure*; ``[job] sha`` builds it with ``run build``'s fetch and build, an
env reused by its key), submits one array per grant and writes ``<job>.submission.json``::

    {"stamp": ..., "placements": [{"grant", "stamp", "account", "targets", "items": [start, stop], "run_dir",
                                   "job_ids", "shards", "plan"}, ...],
     "moved": [<a placement whose grant stopped answering>, ...]}

``status``/``gather`` walk the placements and merge them into one result, item indices back in the batch's
order. A grant whose login hosts all fail to answer is NAMED, never read as empty.

A DEAD GRANT'S SLICE IS RE-PLACED BY THE RETRY RULE. ``retry`` re-submits what has no results file and is
not active -- on a grant that answers, its own shards, as before. On a grant that does not answer nothing is
known to have returned or to be running, so ALL its shards meet that rule: its slice is spanned again over
the grants still answering and the old placement moves to ``moved``. Nothing is reserved anywhere, so a
re-placed slice is an ordinary new submission.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from lab_commons.hpc.cluster import Runner, Unreachable, gather, probe, shard_states, submit
from lab_commons.hpc.config import Config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import Plan
from lab_commons.hpc.run import ACTIVE
from lab_commons.hpc.shell import ENV
from lab_commons.hpc.span import Placement, span

__all__ = [
    'Ensure',
    'Reach',
    'built_config',
    'gather_job',
    'plan_job',
    'retry_job',
    'settled',
    'shard_counts',
    'status_job',
    'status_lines',
    'submit_job',
]

#: A runner for one grant -- ``ssh`` to its login hosts in a CLI, a fake in a test.
type Reach = Callable[[Grant], Runner]

#: Make *grant*'s home hold the job's tree and env; raise ``RuntimeError`` (with why) when it cannot.
type Ensure = Callable[[Grant], None]


def built_config(config: Config) -> Config:
    """With ``[job] sha``, the job runs in ``~/ci/trees/<sha>`` with its env sourced first; else *config* as is."""
    job = config.job
    if not job.sha:
        return config
    if job.workdir != '~':
        msg = f'[job] sha names the tree (~/ci/trees/{job.sha}); drop [job] workdir = {job.workdir!r}'
        raise ValueError(msg)
    return replace(config, job=replace(job, workdir=f'~/ci/trees/{job.sha}', setup=(ENV, *job.setup)))


def _answering(grants: tuple[Grant, ...], reach: Reach) -> list[tuple[Grant, Any]]:
    """Each grant that answers, with its snapshot; a grant whose every login host is silent is left out."""
    found = []
    for grant in grants:
        try:
            found.append((grant, probe(reach(grant), grant.slurm_account)))
        except Unreachable:
            continue
    return found


def plan_job(config: Config, n_items: int, machine: Machine, reach: Reach) -> tuple[Placement, ...]:
    """Where *n_items* of *config* would run now."""
    return span(
        n_items,
        config.cost,
        _answering(machine.grants, reach),
        session=machine.session,
        policy=config.policy,
    )


def _place(
    config: Config,
    items: list[Any],
    offset: int,
    grants: tuple[Grant, ...],
    *,
    machine: Machine,
    reach: Reach,
    ensure: Ensure | None,
    stamp: str,
) -> list[dict[str, Any]]:
    """Span *items* (the batch's ``[offset, ...)``) over *grants*, ensure each tree, submit; their records.

    A grant whose tree cannot be made is dropped and the span recomputed without it.
    """
    usable = list(grants)
    while True:
        placements = span(
            len(items),
            config.cost,
            _answering(tuple(usable), reach),
            session=machine.session,
            policy=config.policy,
        )
        failed = []
        for placement in placements:
            if ensure is None:
                continue
            try:
                ensure(placement.grant)
            except (RuntimeError, Unreachable):
                failed.append(placement.grant)
        if not failed:
            break
        usable = [g for g in usable if g not in failed]
    records = []
    for n, placement in enumerate(placements):
        start, stop = placement.items
        tag = f'{stamp}-{n}'
        sub = submit(reach(placement.grant), placement.plan, config, items[start:stop], stamp=tag)
        records.append(
            {
                'grant': placement.grant.name,
                'stamp': tag,
                'account': list(placement.grant.account),
                'targets': list(placement.grant.targets),
                'items': [offset + start, offset + stop],
                'run_dir': sub.run_dir,
                'job_ids': [sub.job_id],
                'shards': sub.shards,
                'plan': asdict(placement.plan),
            }
        )
    return records


def submit_job(
    config: Config, items: list[Any], machine: Machine, reach: Reach, *, stamp: str, ensure: Ensure | None = None
) -> dict[str, Any]:
    """Span, ensure, submit -- the submission record."""
    placed = _place(config, items, 0, machine.grants, machine=machine, reach=reach, ensure=ensure, stamp=stamp)
    return {'stamp': stamp, 'placements': placed, 'moved': []}


def _grant_of(placement: dict[str, Any], machine: Machine) -> Grant:
    """The machine's grant for a recorded placement; its recorded targets when the machine no longer holds it."""
    account = tuple(placement['account'])
    known = next((g for g in machine.grants if g.account == account), None)
    if known is not None:
        return known
    hosts = tuple(t.split('@', 1)[1] for t in placement['targets'])
    return Grant(user=account[0], hosts=hosts, slurm_account=account[1])


def shard_counts(
    record: dict[str, Any], machine: Machine, reach: Reach
) -> list[tuple[str, dict[str, int] | Unreachable]]:
    """Each placement's grant with its shards counted by Slurm state, or why its grant did not answer."""
    found: list[tuple[str, dict[str, int] | Unreachable]] = []
    for placement in record['placements']:
        try:
            states = shard_states(reach(_grant_of(placement, machine)), placement['job_ids'])
        except Unreachable as silent:
            found.append((placement['grant'], silent))
            continue
        counts: dict[str, int] = {}
        for shard in range(placement['shards']):
            state = states.get(shard, 'UNKNOWN')
            counts[state] = counts.get(state, 0) + 1
        found.append((placement['grant'], counts))
    return found


def status_lines(found: list[tuple[str, dict[str, int] | Unreachable]]) -> list[str]:
    """One line per placement of :func:`shard_counts`: its shards by state, or that its grant did not answer."""
    return [
        f'{grant}: did not answer'
        if isinstance(counts, Unreachable)
        else f'{grant}: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items()))
        for grant, counts in found
    ]


def settled(found: list[tuple[str, dict[str, int] | Unreachable]]) -> bool:
    """Every grant answered and none of its shards is still :data:`ACTIVE` (queued or running)."""
    return all(not isinstance(c, Unreachable) and not ACTIVE.intersection(c) for _g, c in found)


def status_job(record: dict[str, Any], machine: Machine, reach: Reach) -> list[str]:
    """One line per placement: its shards by Slurm state, or that its grant did not answer."""
    return status_lines(shard_counts(record, machine, reach))


def gather_job(record: dict[str, Any], machine: Machine, reach: Reach) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Every item result returned so far, in batch order; the shards returned; the grants that did not answer."""
    merged, returned, silent = [], 0, []
    for placement in record['placements']:
        try:
            results = gather(reach(_grant_of(placement, machine)), placement['run_dir'])
        except Unreachable:
            silent.append(placement['grant'])
            continue
        returned += len(results)
        offset = placement['items'][0]
        merged += [{**r, 'index': offset + r['index']} for shard in results.values() for r in shard]
    return sorted(merged, key=lambda r: r['index']), returned, silent


def retry_job(
    config: Config,
    items: list[Any],
    record: dict[str, Any],
    machine: Machine,
    reach: Reach,
    *,
    ensure: Ensure | None = None,
) -> list[str]:
    """Re-submit every shard with no results file that is not active; a silent grant's slice is re-placed.

    Mutates *record*; returns one line per action.
    """
    said, dead = [], []
    for placement in record['placements']:
        run = reach(_grant_of(placement, machine))
        try:
            states = shard_states(run, placement['job_ids'])
            returned = gather(run, placement['run_dir'])
        except Unreachable:
            dead.append(placement)
            continue
        redo = [s for s in range(placement['shards']) if s not in returned and states.get(s) not in ACTIVE]
        if not redo:
            continue
        raw = placement['plan']
        plan = Plan(
            **{
                **raw,
                'shards': tuple(map(tuple, raw['shards'])),
                'partitions': tuple(raw['partitions']),
                'setup': tuple(raw['setup']),
            }
        )
        sub = submit(run, plan, config, [], stamp=placement['stamp'], only=redo)
        placement['job_ids'].append(sub.job_id)
        said.append(f'{placement["grant"]}: resubmitted {len(redo)} shards as job {sub.job_id}')
    alive = tuple(g for g in machine.grants if g.account not in {tuple(p['account']) for p in dead})
    for n, placement in enumerate(dead):
        start, stop = placement['items']
        placed = _place(
            config,
            items[start:stop],
            start,
            alive,
            machine=machine,
            reach=reach,
            ensure=ensure,
            stamp=f'{record["stamp"]}-m{len(record["moved"])}{n}',
        )
        record['placements'].remove(placement)
        record['moved'].append(placement)
        record['placements'] += placed
        said.append(
            f'{placement["grant"]} did not answer: items {start}-{stop} re-placed on '
            + json.dumps([p['grant'] for p in placed])
        )
    return said or ['nothing to retry']
