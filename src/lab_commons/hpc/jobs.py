"""A job file's batch over every grant it spans: submit, status, gather, retry -- one record, many grants.

ONE SUBMISSION, ONE RECORD. ``submit`` spans the items (:func:`lab_commons.hpc.span.span`), makes sure each
chosen grant has the job's artifact (*ensure*; ``[job] sha`` builds that commit's one file,
:mod:`lab_commons.hpc.artifact`), submits one array per grant and writes ``<job>.submission.json``::

    {"stamp": ..., "placements": [{"grant", "stamp", "account", "targets", "items": [start, stop], "run_dir",
                                   "job_ids", "shards", "plan"}, ...],
     "moved": [<a placement whose grant stopped answering>, ...]}

``status``/``gather`` walk the placements and merge them into one result, item indices back in the batch's
order. A grant whose login hosts all fail to answer is NAMED, never read as empty.

A JOB HAS A FILE BUDGET (``[policy] file_budget``): ``gather`` counts each placement's run directory on the
shared home and names every one over it -- the job fails. A run an earlier worker wrote (one results file
per shard) is upgraded on read and named as such; its file count was that worker's, so it is not judged.

A DEAD GRANT'S SLICE IS RE-PLACED BY THE RETRY RULE. ``retry`` re-submits what has no results file and is
not active -- on a grant that answers, its own shards, as before. On a grant that does not answer nothing is
known to have returned or to be running, so ALL its shards meet that rule: its slice is spanned again over
the grants still answering and the old placement moves to ``moved``. Nothing is reserved anywhere, so a
re-placed slice is an ordinary new submission.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from lab_commons.hpc.affinity import place
from lab_commons.hpc.artifact import enter_lines, require
from lab_commons.hpc.budget import ask_of, charge
from lab_commons.hpc.cluster import Runner, Unreachable, files_of, gather, probe, submit
from lab_commons.hpc.config import Config, Cost
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import Plan, Unplaceable, plan_on
from lab_commons.hpc.run import ACTIVE
from lab_commons.hpc.sacct import shard_states
from lab_commons.hpc.slurm import Snapshot
from lab_commons.hpc.span import Placement, span
from lab_commons.hpc.topology import Lane, architecture

__all__ = [
    'Ensure',
    'Gathered',
    'Reach',
    'built_config',
    'gather_job',
    'grant_of',
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

#: Make *grant*'s home hold the job's artifact; raise ``RuntimeError`` (with why) when it cannot.
type Ensure = Callable[[Grant], None]


def built_config(config: Config) -> Config:
    """With ``[job] sha``, every task first unpacks and enters that commit's artifact; else *config* as is.

    Raises:
        lab_commons.hpc.artifact.ArtifactRefused: ``[job] artifact`` is not a format, or one not built yet.
        ValueError: a ``workdir`` beside ``sha`` -- the artifact is where the job runs -- or in a test run tree.

    """
    job = config.job
    if '/ci/trees/' in f'/{job.workdir.lstrip("~")}':
        msg = (
            f'[job] workdir = {job.workdir!r} is a test run tree, retired for jobs: declare [job] sha and '
            'repo_url and the job runs from that commit artifact, one file (lab_commons.hpc.artifact)'
        )
        raise ValueError(msg)
    if not job.sha:
        return config
    require(job.artifact)
    if job.workdir != '~':
        msg = f'[job] sha runs the job in its artifact; drop [job] workdir = {job.workdir!r}'
        raise ValueError(msg)
    return replace(config, job=replace(job, setup=(*enter_lines(job.sha, job.artifact), *job.setup)))


def _answering(grants: tuple[Grant, ...], reach: Reach) -> list[tuple[Grant, Any]]:
    """Each grant that answers, with its snapshot; a grant whose every login host is silent is left out."""
    found = []
    for grant in grants:
        try:
            found.append((grant, probe(reach(grant), grant.slurm_account)))
        except Unreachable:
            continue
    return found


def _holding(
    config: Config, n_items: int, answering: list[tuple[Grant, Any]], machine: Machine
) -> tuple[list[tuple[Grant, Any]], str]:
    """The answering grants whose ceiling holds the padded wall, and why each other one does not.

    Judged per grant, never on a span's pick: a wall clamped to a SHORT ceiling plans a shorter makespan,
    so the span would prefer the grant that cuts the work.
    """
    wall = config.cost.seconds * config.policy.safety
    holders, why = [], []
    for grant, snapshot in answering:
        try:
            shape = _shape(config, grant, snapshot)
            plan = plan_on(
                shape.grant,
                snapshot,
                shape.rounds(n_items),
                shape.cost,
                session=machine.session,
                policy=config.policy,
            )
        except Unplaceable as refused:
            why.append(str(refused))
            continue
        if plan.top_minutes is None or wall <= plan.top_minutes * 60:
            holders.append((grant, snapshot))
        else:
            why.append(
                f'{grant.name}: a {wall / 60:.0f}-min padded wall exceeds its {plan.top_minutes:.0f}-min ceiling'
            )
    return holders, '; '.join(why) or 'no free grant answers'


@dataclass(frozen=True)
class _Shape:
    """One task of a job on one grant: the grant narrowed to its node class, one task's cost, and its lanes."""

    grant: Grant
    cost: Cost
    lanes: tuple[Lane, ...] = ()

    def rounds(self, n_items: int) -> int:
        """How many rounds of one item a lane *n_items* is: the unit a task's cost and time are planned in."""
        return -(-n_items // max(1, len(self.lanes)))


def _shape(config: Config, grant: Grant, snapshot: Snapshot) -> _Shape:
    """One task of *config* on *grant*, its lanes derived from the grant's architecture in the cluster catalog.

    Raises:
        Unplaceable: the grant's cluster cannot hold the shape -- it names no architecture, no node class
            describes its partitions, Slurm reports another node width, or the lanes do not tile the node; or
            an exclusive one-item task is narrower than its nodes, which Slurm would charge whole.
        ValueError: the catalog is missing, malformed, or has no entry the grant names -- a defect, not room.

    """
    task = config.task
    if task.lane_domains is None:
        if task.exclusive and (wider := _wider_nodes(config.cost.cpus, grant, snapshot)) is not None:
            raise Unplaceable(wider)
        return _Shape(grant, config.cost)
    if not grant.architecture:
        msg = f'{grant.name} names no architecture; [task] lane_domains needs its entry in the cluster catalog'
        raise Unplaceable(msg)
    try:
        arch = architecture(grant.architecture)
    except FileNotFoundError as missing:
        raise ValueError(str(missing)) from missing
    partitions = grant.partitions or tuple(snapshot.partitions)
    node = arch.node_class(partitions)
    if node is None:
        msg = f'{grant.name}: no node class of {arch.name} describes its partitions {list(partitions)}'
        raise Unplaceable(msg)
    held = tuple(p for p in partitions if p in node.partitions)
    reported = {n.total_cpus for n in snapshot.nodes if n.schedulable and not set(held).isdisjoint(n.partitions)}
    if reported - {node.cpus}:
        msg = (
            f'{grant.name}: Slurm reports {sorted(reported)}-CPU nodes in {list(held)}, but the catalog '
            f'gives {arch.name} {node.name} {node.cpus} CPUs; correct the catalog'
        )
        raise Unplaceable(msg)
    try:
        lanes = node.lanes(task.lane_domains)
    except ValueError as refused:
        msg = f'{grant.name}: {refused}'
        raise Unplaceable(msg) from refused
    item, n = config.cost, len(lanes)
    cost = Cost(cpus=node.cpus, mem_gb=item.mem_gb * n, gpus=item.gpus * n, seconds=item.seconds)
    return _Shape(replace(grant, partitions=held), cost, lanes)


def _in_items(placement: Placement, grant: Grant, shape: _Shape, config: Config, n_items: int) -> Placement:
    """*placement*, planned in rounds, back in items on *grant* -- stamped with the task's shape."""
    lanes = max(1, len(shape.lanes))
    start, stop = placement.items
    first, last = start * lanes, min(stop * lanes, n_items)
    shards = tuple((a * lanes, min(b * lanes, last - first)) for a, b in placement.plan.shards)
    plan = replace(
        placement.plan,
        shards=shards,
        exclusive=config.task.whole_node,
        lane_cpus=tuple(lane.cpus for lane in shape.lanes),
        lane_mems=tuple(lane.mems for lane in shape.lanes),
    )
    return Placement(grant, plan, (first, last))


def _wider_nodes(cpus: int, grant: Grant, snapshot: Snapshot) -> str | None:
    """Why an exclusive task of *cpus* may not run on *grant*: a node of its partitions is wider; else ``None``.

    Slurm charges an exclusive task its WHOLE node, so a task priced below the node's CPUs would spend quota
    the plan never counted. A narrower node cannot hold the task at all, so it is no risk.
    """
    names = set(grant.cluster().partitions or snapshot.partitions)
    wider = {
        n.total_cpus
        for n in snapshot.nodes
        if n.schedulable and not names.isdisjoint(n.partitions) and n.total_cpus > cpus
    }
    if not wider:
        return None
    return (
        f'{grant.name}: an exclusive task of {cpus} CPU would be charged a whole {max(wider)}-CPU node; '
        f'set [cost] cpus to the node, or cut it into lanes with [task] lane_domains'
    )


def _spread(
    config: Config, n_items: int, candidates: list[tuple[Grant, Any]], machine: Machine
) -> tuple[Placement, ...]:
    """*n_items* spanned over the *candidates* that hold the task's shape, planned in rounds, returned in items.

    One span plans one task cost, so every candidate must derive the SAME shape; candidates on architectures
    that cut the task differently are refused together rather than one silently preferred.
    """
    shaped, why = [], []
    for grant, snapshot in candidates:
        try:
            shaped.append((grant, snapshot, _shape(config, grant, snapshot)))
        except Unplaceable as refused:
            why.append(str(refused))
    if candidates and not shaped:
        raise Unplaceable('; '.join(why))
    if len({(s.cost, s.lanes) for _, _, s in shaped}) > 1:
        msg = 'the candidate grants cut this task differently (several architectures); name one architecture'
        raise Unplaceable(msg)
    by_account = {s.grant.account: (g, s) for g, _, s in shaped}
    shape = shaped[0][2] if shaped else _Shape(Grant('-', ('-',), '-'), config.cost)
    rounds = span(
        shape.rounds(n_items),
        shape.cost,
        [(s.grant, snapshot) for _, snapshot, s in shaped],
        session=machine.session,
        policy=config.policy,
        paging=machine.paging,
        rerun_safe=config.job.rerun_safe,
    )
    return tuple(_in_items(p, *by_account[p.grant.account], config, n_items) for p in rounds)


def tiered(
    config: Config, n_items: int, grants: tuple[Grant, ...], *, machine: Machine, reach: Reach
) -> tuple[Placement, ...]:
    """Span over the free candidates in *grants*; over the billed ones only for work the free TIER cannot hold.

    The free tier is every free grant *machine* declares, candidate or not: a free grant ``_place`` dropped
    for another reason still says whether the work needs money. Billed is used only when EVERY free grant
    answered and none holds the work -- the span refuses for room (:class:`Unplaceable`) or the padded wall
    exceeds every free ceiling. A silent free grant refuses rather than bills; billed and free never share a
    span; any other ``ValueError`` is the request's defect and propagates.
    """

    def over(candidates: list[tuple[Grant, Any]]) -> tuple[Placement, ...]:
        return place(candidates, machine.affinity, lambda c: _spread(config, n_items, c, machine))

    billed = [g for g in grants if g.billed]
    tier = [g for g in machine.grants if not g.billed]
    if not tier:
        return over(_answering(tuple(billed), reach))
    answering = _answering(tuple(tier), reach)
    silent = [g.name for g in tier if all(g is not a for a, _ in answering)]
    holders, reason = _holding(config, n_items, answering, machine)
    if holders:
        mine = [(g, s) for g, s in holders if g in grants]
        if not mine:
            msg = 'the free tier holds this work but none of its grants is a candidate; billed is not for that'
            raise ValueError(msg)
        return over(mine)
    if not billed:
        mine = [(g, s) for g, s in answering if g in grants]
        if not mine:
            raise Unplaceable(reason)
        return over(mine)
    if silent:
        msg = (
            f'{reason}; free grant(s) {", ".join(silent)} silent -- a billed grant never pays for unreachable free room'
        )
        raise Unplaceable(msg)
    paid = _answering(tuple(billed), reach)
    if not paid:
        msg = f'{reason}; no billed grant answers'
        raise Unplaceable(msg)
    return over(paid)


def plan_job(config: Config, n_items: int, machine: Machine, reach: Reach) -> tuple[Placement, ...]:
    """Where *n_items* of *config* would run now."""
    return tiered(config, n_items, machine.grants, machine=machine, reach=reach)


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

    A grant whose tree cannot be made is dropped and the span recomputed without it; when none is left,
    the refusal names each grant's failure, never an empty machine.
    """
    usable, why = list(grants), []
    while True:
        if not usable:
            msg = 'no grant can take this work: ' + '; '.join(why)
            raise ValueError(msg)
        placements = tiered(config, len(items), tuple(usable), machine=machine, reach=reach)
        failed = []
        for placement in placements:
            if ensure is None:
                continue
            try:
                ensure(placement.grant)
            except (RuntimeError, Unreachable) as refused:
                failed.append(placement.grant)
                why.append(str(refused))
        if not failed:
            break
        usable = [g for g in usable if g not in failed]
    records = []
    for n, placement in enumerate(placements):
        start, stop = placement.items
        tag = f'{stamp}-{n}'
        run = reach(placement.grant)
        charge(run, placement.grant, ask_of(placement.plan))
        sub = submit(run, placement.plan, config, items[start:stop], stamp=tag)
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


def grant_of(placement: dict[str, Any], machine: Machine) -> Grant:
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
            states = shard_states(reach(grant_of(placement, machine)), placement['job_ids'])
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


@dataclass(frozen=True)
class Gathered:
    """What :func:`gather_job` read: item results in batch order, and what the caller must name."""

    results: list[dict[str, Any]]
    #: Shards returned, over every placement that answered.
    returned: int
    #: Grants that did not answer: their shards are not in :attr:`results`.
    silent: list[str]
    #: One line per run directory holding more files than ``[policy] file_budget``: the job FAILS.
    over: list[str]
    #: Run directories an earlier worker wrote, read through the upgrade and not judged by the budget.
    earlier: list[str]


def gather_job(record: dict[str, Any], machine: Machine, reach: Reach, *, budget: int) -> Gathered:
    """Every placement's returned results, merged, with its run directory's files counted against *budget*."""
    merged, returned, silent, over, earlier = [], 0, [], [], []
    for placement in record['placements']:
        run, where = reach(grant_of(placement, machine)), placement['run_dir']
        try:
            results = gather(run, where)
            files, upgraded = files_of(run, where)
        except Unreachable:
            silent.append(placement['grant'])
            continue
        if upgraded:
            earlier.append(f'{placement["grant"]}: {where}')
        elif files > budget:
            over.append(f'{placement["grant"]}: {where} holds {files} files, over file_budget = {budget}')
        returned += len(results)
        offset = placement['items'][0]
        merged += [{**r, 'index': offset + r['index']} for shard in results.values() for r in shard]
    return Gathered(sorted(merged, key=lambda r: r['index']), returned, silent, over, earlier)


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
        run = reach(grant_of(placement, machine))
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
                'lane_cpus': tuple(raw['lane_cpus']),
                'lane_mems': tuple(raw['lane_mems']),
            }
        )
        charge(run, grant_of(placement, machine), ask_of(plan, len(redo)))
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
