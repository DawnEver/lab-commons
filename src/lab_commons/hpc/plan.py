"""Cut N independent items into one Slurm job array sized to the free holes and the caller's quota. Pure.

THE UNIT IS ONE ITEM'S COST, AND A SHARD IS ONE SLOT WIDE. An array task asks for exactly what one item
needs (``cost.cpus``, ``cost.mem_gb``, ``cost.gpus``) and runs its items back to back. A narrow task is
what fits the scattered free cores of a busy cluster -- measured on a real cluster (2026-10-08): 1599 cores idle in
``defq`` and not one whole node -- and it is what Slurm's backfill can slot in. A caller whose item is
itself parallel says so in ``cost.cpus``; nothing here invents a width.

THREE NUMBERS ARE DECIDED, IN THIS ORDER.

1. **Shards** -- consecutive items are packed until a shard holds enough padded time to amortise start-up
   (``shard_minutes_min``) without passing ``shard_minutes_max``; both bounds are then grown until the
   array fits ``max_array``, the QOS's per-user submit limit (each array task is one job to Slurm) and
   what the quota CEILING runs at once -- ONE WAVE, so the makespan is the wall and never longer; a
   second wave is cut only when one wave's wall would break the time ceiling.
   An item's time is MEASURED when the caller has it (``seconds``, one per item) and ``cost.seconds``
   otherwise; the wall limit is the SLOWEST shard's padded sum, never an average. Measured on a real cluster
   (2026-10-09): a flat 120 s per test file planned 129-min shards that ran 1h25-2h07, and 23 of 99 hit
   the limit. Decided per candidate, because the QOS differs.
2. **Partitions** -- every candidate whose time ceiling (partition AND QOS) holds the shard's padded wall
   time is admissible; the first, in the caller's order, with a free slot now (else the first) fixes the
   QOS, and the plan asks for EVERY admissible candidate under that QOS as one ``--partition=a,b``, so
   Slurm starts each task wherever it fits first. Measured 2026-10-10: array 7660516 named ``shortq``
   alone and pended for hours while ``defq``, declared beside it, had idle nodes. One ``sbatch`` carries
   one ``--qos``, so a candidate needing another QOS is never merged in.
3. **Throttle** -- concurrent tasks = what the quota CEILING allows, so Slurm never holds tasks the caller
   could not run anyway. What the caller's running jobs hold, and the free slots, only inform the makespan
   estimate: both change by the minute, Slurm pends a task over the limit rather than refusing it, and
   the array is submitted once.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Final

from lab_commons.hpc.config import GIB, Cluster, Cost, Limits, Policy
from lab_commons.hpc.grants import COMMENT_PREFIX, Grant
from lab_commons.hpc.slurm import Snapshot
from lab_commons.resources import CPU, GPU, MEMORY
from lab_commons.width import fits

#: Slurm reports memory in MiB; :data:`~lab_commons.resources.MEMORY` counts bytes.
MIB: Final = 1024**2

__all__ = [
    'FILES_PER_RUN',
    'FILES_PER_SHARD',
    'MIB',
    'Plan',
    'allocate',
    'balance',
    'files_needed',
    'free_slots',
    'grantable_gb',
    'headroom',
    'make_plan',
    'plan_on',
    'quota_slots',
]


@dataclass(frozen=True)
class Plan:
    """Everything ``sbatch`` needs, plus the estimate a human reads before submitting."""

    #: Every partition the array may start in, the grant's declared order kept -- ``--partition=a,b``.
    partitions: tuple[str, ...]
    qos: str
    account: str
    cpus: int
    mem_mb: int
    gpus: int
    minutes: int
    shards: tuple[tuple[int, int], ...]
    throttle: int
    free_slots: int
    makespan_minutes: float
    #: The ``--comment`` every task carries -- the workstation tag :func:`allocate` stamps.
    comment: str = ''
    #: The grant's compute-node set-up (``module load ...``), run before the job's own; :func:`allocate` stamps it.
    setup: tuple[str, ...] = ()

    def describe(self) -> str:
        """One paragraph: what will be asked for and how long it should take."""
        gpu = f', {self.gpus} GPU' if self.gpus else ''
        return (
            f'{len(self.shards)} array tasks on {",".join(self.partitions)} '
            f'(qos {self.qos or "default"}, account {self.account}); '
            f'each {self.cpus} CPU, {self.mem_mb / 1024:.1f} GB{gpu}, {self.minutes} min wall; '
            f'up to {self.throttle} at once ({self.free_slots} slots free now); '
            f'estimated makespan {self.makespan_minutes:.0f} min.'
        )


def free_slots(snapshot: Snapshot, partitions: Collection[str], cost: Cost) -> int:
    """How many one-item tasks the schedulable nodes of any of *partitions* could start now, each node once."""
    demand = cost.demands()
    return sum(
        fits({CPU.name: n.cpus, MEMORY.name: n.mem_mb * MIB, GPU.name: n.gpus}, demand)
        for n in snapshot.nodes
        if n.schedulable and not set(partitions).isdisjoint(n.partitions)
    )


def quota_slots(snapshot: Snapshot, cost: Cost, limits: Limits, *, remaining: bool = False) -> int:
    """One-item tasks the caller may run at once -- by the CEILING, or by what is left of it now.

    ``[limits]`` lowers either; it never raises one.
    """
    quota = snapshot.quota
    left = quota.remaining()
    cpus, mem_mb, gpus = (
        (left['cpu'], left['mem'], left['gpu']) if remaining else (quota.cpus, quota.mem_mb, quota.gpus)
    )
    capacity = {
        CPU.name: _lower(cpus, limits.cpus),
        MEMORY.name: _lower(
            None if mem_mb is None else mem_mb * MIB,
            None if limits.mem_gb is None else limits.mem_gb * GIB,
        ),
        GPU.name: _lower(gpus, limits.gpus),
    }
    if all(v is None for v in capacity.values()):
        msg = f'account {quota.account!r} reports no ceiling at all; set [limits] so the throttle is bounded'
        raise ValueError(msg)
    return fits(capacity, cost.demands())


def _lower(cluster: float | None, local: float | None) -> float | None:
    """``[limits]`` may only LOWER the cluster's ceiling; a missing side is no constraint."""
    values = [v for v in (cluster, local) if v is not None]
    return min(values) if values else None


def _ceiling(snapshot: Snapshot, partition: str, qos: str) -> float | None:
    """The tighter of the partition's and the QOS's time limits, in minutes."""
    part = snapshot.partitions[partition].max_minutes
    q = snapshot.quota.qos_minutes.get(qos)
    values = [v for v in (part, q) if v is not None]
    return min(values) if values else None


def _pack(seconds: Sequence[float], low: float, high: float) -> list[tuple[int, int]]:
    """Consecutive ranges: close a shard once it holds *low* seconds, never let one pass *high* unless alone."""
    shards: list[tuple[int, int]] = []
    start, held = 0, 0.0
    for index, cost in enumerate(seconds):
        if index > start and (held >= low or held + cost > high):
            shards.append((start, index))
            start, held = index, 0.0
        held += cost
    shards.append((start, len(seconds)))
    return shards


def _shards(seconds: Sequence[float], policy: Policy, room: int | None) -> list[tuple[int, int]]:
    """The policy window first, then both bounds grown until the array fits ``max_array`` and *room*."""
    cap = policy.max_array if room is None else min(policy.max_array, room)
    low, high = policy.shard_minutes_min * 60, policy.shard_minutes_max * 60
    floor = sum(seconds) / cap
    low, high = max(low, floor), max(high, floor)
    while len(shards := _pack(seconds, low, high)) > cap:
        low, high = low * 1.1, high * 1.1
    return shards


def balance(
    seconds: Sequence[float], shards: Sequence[tuple[int, int]]
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    """The same NUMBER of shards, refilled longest-processing-time first: ``(order, shards)``.

    *order* permutes the items so each LPT bin is a consecutive range (the array script slices ranges);
    within a bin the items keep their original order. The refill is kept only when its slowest shard is
    faster than the given one's, else the input comes back unchanged. Measured 2026-10-11: consecutive
    ranges of collection order put the heaviest files of one directory in one shard -- a 72-min shard
    beside a median of 8, so the round's wall was its straggler.
    """
    slowest = max((sum(seconds[a:b]) for a, b in shards), default=0.0)
    bins: list[list[int]] = [[] for _ in shards]
    loads = [(0.0, n) for n in range(len(shards))]
    for index in sorted(range(len(seconds)), key=lambda i: -seconds[i]):
        load, n = heapq.heappop(loads)
        bins[n].append(index)
        heapq.heappush(loads, (load + seconds[index], n))
    if not bins or max(load for load, _ in loads) >= slowest:
        return list(range(len(seconds))), tuple(shards)
    order, ranges, start = [], [], 0
    for members in bins:
        order += sorted(members)
        ranges.append((start, start + len(members)))
        start += len(members)
    return order, tuple(ranges)


def _wall(seconds: Sequence[float], shards: Sequence[tuple[int, int]]) -> int:
    """The slowest shard's padded sum, in whole minutes."""
    return max(1, math.ceil(max(sum(seconds[a:b]) for a, b in shards) / 60))


def _one_wave(
    padded: Sequence[float],
    policy: Policy,
    room: int | None,
    *,
    at_once: int,
    ceiling: float | None,
    min_minutes: float,
) -> tuple[list[tuple[int, int]], int]:
    """Shards no more than run *at_once*, so the makespan IS the wall; more only when *ceiling* forces it.

    Measured 2026-10-10: a round planned 24 shards of 21 min for 17 concurrent tasks -- a 42-min makespan
    under a 21-min wall, so every task was killed at half the time the round itself expected to take.
    A second wave buys nothing a longer wall does not, and the longer wall is what a slow id needs.
    *min_minutes* (a retry after a TIMEOUT) raises the wall, held to *ceiling*.
    """
    cap = at_once if room is None else min(room, at_once)
    shards = _shards(padded, policy, cap)
    if ceiling is not None and _wall(padded, shards) > ceiling:
        shards = _shards(padded, policy, room)
    floor = min_minutes if ceiling is None else min(min_minutes, ceiling)
    return shards, max(_wall(padded, shards), math.ceil(floor))


def make_plan(
    n_items: int,
    cost: Cost,
    snapshot: Snapshot,
    *,
    cluster: Cluster,
    policy: Policy,
    limits: Limits,
    seconds: Sequence[float] | None = None,
    min_minutes: float = 0.0,
) -> Plan:
    """Decide shards, partition and throttle for *n_items* -- see the module docstring.

    *seconds* is each item's measured time (``cost.seconds`` where absent); the margin is ``policy.safety``.
    *min_minutes* is the least wall a task may ask for -- a retry after a TIMEOUT -- held to each
    candidate's ceiling; the candidates granting the longest wall are preferred.
    """
    if seconds is not None and len(seconds) != n_items:
        msg = f'{len(seconds)} measured times for {n_items} items'
        raise ValueError(msg)
    if n_items < 1:
        msg = 'nothing to plan: the item list is empty'
        raise ValueError(msg)
    allowed = quota_slots(snapshot, cost, limits)
    if allowed < 1:
        msg = f'the quota ceiling of account {snapshot.quota.account!r} cannot fit even one item of {cost}'
        raise ValueError(msg)
    padded = [s * policy.safety for s in (seconds or [cost.seconds] * n_items)]
    candidates = cluster.partitions or tuple(snapshot.partitions)
    options = []
    refusals = []
    for name in candidates:
        part = snapshot.partitions.get(name)
        if part is None or not part.up:
            refusals.append(f'{name}: not up')
            continue
        qos = cluster.qos.get(name, snapshot.quota.default_qos)
        if part.allow_qos and qos not in part.allow_qos:
            refusals.append(f'{name}: qos {qos!r} not allowed')
            continue
        room = snapshot.quota.submit_room(qos)
        if room == 0:
            refusals.append(f'{name}: qos {qos!r} submit limit already reached')
            continue
        ceiling = _ceiling(snapshot, name, qos)
        at_once = min(allowed, snapshot.quota.qos_max_jobs.get(qos) or allowed)
        shards, minutes = _one_wave(padded, policy, room, at_once=at_once, ceiling=ceiling, min_minutes=min_minutes)
        if ceiling is not None and minutes > ceiling:
            refusals.append(f'{name}: a {minutes}-min shard exceeds its {ceiling:.0f}-min ceiling')
            continue
        options.append((name, qos, shards, minutes, ceiling))
    if not options:
        msg = f'no candidate partition can take this work: {"; ".join(refusals)}'
        raise ValueError(msg)
    longest = max(o[3] for o in options)
    holds = [o for o in options if o[3] == longest] if min_minutes else options
    _, qos, chosen, minutes, _ = next((o for o in holds if free_slots(snapshot, (o[0],), cost) > 0), holds[0])
    partitions = tuple(o[0] for o in options if o[1] == qos and (o[4] is None or o[4] >= minutes))
    shards = tuple(chosen)
    max_jobs = snapshot.quota.qos_max_jobs.get(qos)
    throttle = min(len(shards), allowed, max_jobs or len(shards))
    free = free_slots(snapshot, partitions, cost)
    running = max(1, min(throttle, free, quota_slots(snapshot, cost, limits, remaining=True)))
    return Plan(
        partitions=partitions,
        qos=qos,
        account=snapshot.quota.account,
        cpus=cost.cpus,
        mem_mb=math.ceil(cost.mem_gb * 1024),
        gpus=cost.gpus,
        minutes=minutes,
        shards=shards,
        throttle=throttle,
        free_slots=free,
        makespan_minutes=math.ceil(len(shards) / running) * minutes,
    )


def grantable_gb(snapshots: Sequence[tuple[Grant, Snapshot]]) -> float | None:
    """The most memory ONE item may ask of any of these grants -- the largest quota ceiling; ``None`` is no ceiling.

    An ask above it is refused by every grant (:func:`make_plan`), so a retry is held to it. Measured
    2026-10-14 (Ada): an item OOM-killed at 62.7 GB was re-asked at 366.77 GB against a 360 GB ceiling,
    and the run could never advance again.
    """
    ceilings = [snapshot.quota.mem_mb for _, snapshot in snapshots]
    if not ceilings or None in ceilings:
        return None
    return max(c for c in ceilings if c is not None) / 1024


def headroom(snapshot: Snapshot) -> int:
    """CPUs still free on the account: ``quota - held``, a missing quota leaving the schedulable nodes' free CPUs.

    *held* is every CPU the caller's jobs on the account hold, tagged or not. No box's share is carved out in
    advance: admission fits each job against what is free now.
    """
    quota = snapshot.quota.cpus
    if quota is None:
        cap = float(sum(node.cpus for node in snapshot.nodes if node.schedulable))
    else:
        cap = quota - sum(snapshot.usage.values())
    return max(0, math.floor(cap))


#: The files a submitted array adds to the home: its run directory, ``logs/``, ``results/``, manifest, script.
FILES_PER_RUN: Final = 5

#: And each array task: its log and its results file.
FILES_PER_SHARD: Final = 2


def files_needed(plan: Plan) -> int:
    """The inodes *plan* adds to the home when submitted -- the file-count quota is a resource like the CPUs."""
    return FILES_PER_RUN + FILES_PER_SHARD * len(plan.shards)


def plan_on(
    grant: Grant,
    snapshot: Snapshot,
    n_items: int,
    cost: Cost,
    *,
    workstation: str | None,
    policy: Policy,
    seconds: Sequence[float] | None = None,
    min_minutes: float = 0.0,
) -> Plan:
    """*grant*'s plan for *n_items* on its headroom, stamped with the workstation tag and the grant's set-up.

    Refused (``ValueError``, naming the account and why) when the account has no CPU headroom for one item, no
    partition can hold the work, or the home has fewer free inodes than the array writes (:func:`files_needed`).
    """
    room = headroom(snapshot)
    if room < cost.cpus:
        held = ', '.join(f'{tag or "untagged"}={cpus:g}' for tag, cpus in sorted(snapshot.usage.items()))
        msg = (
            f'{grant.name}: headroom {room} CPU < {cost.cpus} per item '
            f'(quota {snapshot.quota.cpus}, held {held or "nothing"})'
        )
        raise ValueError(msg)
    try:
        plan = make_plan(
            n_items,
            cost,
            snapshot,
            cluster=grant.cluster(),
            policy=policy,
            limits=Limits(cpus=room),
            seconds=seconds,
            min_minutes=min_minutes,
        )
    except ValueError as refused:
        msg = f'{grant.name}: {refused}'
        raise ValueError(msg) from refused
    if snapshot.inodes_free is not None and files_needed(plan) > snapshot.inodes_free:
        msg = f'{grant.name}: the home has {snapshot.inodes_free} free inodes, the array writes {files_needed(plan)}'
        raise ValueError(msg)
    return replace(plan, comment=COMMENT_PREFIX + workstation if workstation else '', setup=grant.setup)


def allocate(
    n_items: int,
    cost: Cost,
    candidates: Sequence[tuple[Grant, Snapshot]],
    *,
    workstation: str | None,
    policy: Policy,
    seconds: Sequence[float] | None = None,
    min_minutes: float = 0.0,
) -> tuple[Grant, Plan]:
    """The ONE grant whose plan finishes EARLIEST (:func:`plan_on`). Pure.

    A tie goes to the grant with MORE headroom -- measured now, pending jobs included (:func:`headroom`) --
    then to the higher ``priority``, then to the earlier grant in the file. Two grants on one cluster plan
    the same makespan for a small batch, so without the headroom key the first in the file took every run
    however loaded it was. No grant with room raises, naming each account and why -- an empty answer is
    never a silent queue. Spreading one batch over SEVERAL grants is :func:`lab_commons.hpc.span.span`.
    """
    options: list[tuple[float, int, int, int, Grant, Plan]] = []
    refusals = []
    for order, (grant, snapshot) in enumerate(candidates):
        try:
            plan = plan_on(
                grant,
                snapshot,
                n_items,
                cost,
                workstation=workstation,
                policy=policy,
                seconds=seconds,
                min_minutes=min_minutes,
            )
        except ValueError as refused:
            refusals.append(str(refused))
            continue
        options.append((plan.makespan_minutes, -headroom(snapshot), -grant.priority, order, grant, plan))
    if not options:
        msg = 'no grant can take this work: ' + '; '.join(refusals or ['this machine holds no grant'])
        raise ValueError(msg)
    *_, grant, plan = min(options, key=lambda o: o[:4])
    return grant, plan
