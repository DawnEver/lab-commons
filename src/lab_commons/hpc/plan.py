"""Cut N independent items into one Slurm job array sized to the free holes and the caller's quota. Pure.

THE UNIT IS ONE ITEM'S COST, AND A SHARD IS ONE SLOT WIDE. An array task asks for exactly what one item
needs (``cost.cpus``, ``cost.mem_gb``, ``cost.gpus``) and runs its items back to back. A narrow task is
what fits the scattered free cores of a busy cluster -- measured on a real cluster (2026-10-08): 1599 cores idle in
``defq`` and not one whole node -- and it is what Slurm's backfill can slot in. A caller whose item is
itself parallel says so in ``cost.cpus``; nothing here invents a width.

THREE NUMBERS ARE DECIDED, IN THIS ORDER.

1. **Shards** -- consecutive items are packed until a shard holds enough padded time to amortise start-up
   (``shard_minutes_min``) without passing ``shard_minutes_max``; both bounds are then grown until the
   array fits ``max_array`` and the QOS's per-user submit limit (each array task is one job to Slurm).
   An item's time is MEASURED when the caller has it (``seconds``, one per item) and ``cost.seconds``
   otherwise; the wall limit is the SLOWEST shard's padded sum, never an average. Measured on a real cluster
   (2026-10-09): a flat 120 s per test file planned 129-min shards that ran 1h25-2h07, and 23 of 99 hit
   the limit. Decided per candidate, because the QOS differs.
2. **Partition** -- the first candidate, in the caller's order, whose time ceiling (partition AND QOS)
   holds the shard's padded wall time; among those, the first that has a free slot now wins over one that
   would queue.
3. **Throttle** -- concurrent tasks = what the quota CEILING allows, so Slurm never holds tasks the caller
   could not run anyway. What the caller's running jobs hold, and the free slots, only inform the makespan
   estimate: both change by the minute, Slurm pends a task over the limit rather than refusing it, and
   the array is submitted once.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from lab_commons.hpc.config import GIB, Cluster, Cost, Limits, Policy
from lab_commons.hpc.grants import COMMENT_PREFIX, Grant
from lab_commons.hpc.slurm import Snapshot
from lab_commons.resources import CPU, GPU, MEMORY
from lab_commons.width import fits

#: Slurm reports memory in MiB; :data:`~lab_commons.resources.MEMORY` counts bytes.
MIB: Final = 1024**2

__all__ = ['MIB', 'Plan', 'allocate', 'free_slots', 'headroom', 'make_plan', 'quota_slots']


@dataclass(frozen=True)
class Plan:
    """Everything ``sbatch`` needs, plus the estimate a human reads before submitting."""

    partition: str
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
            f'{len(self.shards)} array tasks on {self.partition} '
            f'(qos {self.qos or "default"}, account {self.account}); '
            f'each {self.cpus} CPU, {self.mem_mb / 1024:.1f} GB{gpu}, {self.minutes} min wall; '
            f'up to {self.throttle} at once ({self.free_slots} slots free now); '
            f'estimated makespan {self.makespan_minutes:.0f} min.'
        )


def free_slots(snapshot: Snapshot, partition: str, cost: Cost) -> int:
    """How many one-item tasks the partition's schedulable nodes could start right now."""
    demand = cost.demands()
    return sum(
        fits({CPU.name: n.cpus, MEMORY.name: n.mem_mb * MIB, GPU.name: n.gpus}, demand)
        for n in snapshot.nodes
        if n.schedulable and partition in n.partitions
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


def make_plan(
    n_items: int,
    cost: Cost,
    snapshot: Snapshot,
    *,
    cluster: Cluster,
    policy: Policy,
    limits: Limits,
    seconds: Sequence[float] | None = None,
) -> Plan:
    """Decide shards, partition and throttle for *n_items* -- see the module docstring.

    *seconds* is each item's measured time (``cost.seconds`` where absent); the margin is ``policy.safety``.
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
        shards = _shards(padded, policy, room)
        minutes = max(1, math.ceil(max(sum(padded[a:b]) for a, b in shards) / 60))
        ceiling = _ceiling(snapshot, name, qos)
        if ceiling is not None and minutes > ceiling:
            refusals.append(f'{name}: a {minutes}-min shard exceeds its {ceiling:.0f}-min ceiling')
            continue
        options.append((name, qos, shards, minutes))
    if not options:
        msg = f'no candidate partition can take this work: {"; ".join(refusals)}'
        raise ValueError(msg)
    partition, qos, chosen, minutes = next((o for o in options if free_slots(snapshot, o[0], cost) > 0), options[0])
    shards = tuple(chosen)
    max_jobs = snapshot.quota.qos_max_jobs.get(qos)
    throttle = min(len(shards), allowed, max_jobs or len(shards))
    free = free_slots(snapshot, partition, cost)
    running = max(1, min(throttle, free, quota_slots(snapshot, cost, limits, remaining=True)))
    return Plan(
        partition=partition,
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


def headroom(snapshot: Snapshot, share: int, workstation: str) -> int:
    """CPUs this workstation may still claim on a grant: ``min(share, quota - others) - mine``.

    *others* is every CPU the caller's jobs on the account hold that is NOT tagged with *workstation* --
    other boxes, and untagged jobs, which are someone's even if nobody said whose. A missing quota is no
    constraint beyond the share.
    """
    mine = snapshot.usage.get(workstation, 0.0)
    others = sum(cpus for tag, cpus in snapshot.usage.items() if tag != workstation)
    quota = snapshot.quota.cpus
    cap = share if quota is None else min(share, quota - others)
    return max(0, math.floor(cap - mine))


def allocate(
    n_items: int,
    cost: Cost,
    candidates: Sequence[tuple[Grant, Snapshot]],
    *,
    workstation: str,
    policy: Policy,
    seconds: Sequence[float] | None = None,
) -> tuple[Grant, Plan]:
    """The grant whose plan finishes EARLIEST, planned on that grant's headroom. Pure.

    A tie goes to the higher ``priority``, then to the earlier grant in the file. No grant with room
    raises, naming each account and why -- an empty answer is never a silent queue.
    """
    options: list[tuple[float, int, int, Grant, Plan]] = []
    refusals = []
    for order, (grant, snapshot) in enumerate(candidates):
        room = headroom(snapshot, grant.cpus, workstation)
        label = grant.name
        if room < cost.cpus:
            held = ', '.join(f'{tag or "untagged"}={cpus:g}' for tag, cpus in sorted(snapshot.usage.items()))
            refusals.append(
                f'{label}: headroom {room} CPU < {cost.cpus} per item '
                f'(share {grant.cpus}, quota {snapshot.quota.cpus}, held {held or "nothing"})'
            )
            continue
        try:
            plan = make_plan(
                n_items,
                cost,
                snapshot,
                cluster=grant.cluster(),
                policy=policy,
                limits=Limits(cpus=room),
                seconds=seconds,
            )
        except ValueError as refused:
            refusals.append(f'{label}: {refused}')
            continue
        options.append((plan.makespan_minutes, -grant.priority, order, grant, plan))
    if not options:
        msg = 'no grant can take this work: ' + '; '.join(refusals or ['this machine holds no grant'])
        raise ValueError(msg)
    *_, grant, plan = min(options, key=lambda o: o[:3])
    return grant, replace(plan, comment=COMMENT_PREFIX + workstation, setup=grant.setup)
