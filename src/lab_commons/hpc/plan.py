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
3. **Throttle** -- concurrent tasks = the account's HEADROOM now (its ceiling less what every job of the
   caller's on it holds, whatever submitted it) divided by one task's cost, at least one. Measured
   2026-10-16: a 52-task array asked 352 GB a task of a 360 GB ceiling, came out ``%1`` from the ceiling,
   and pended an hour on ``AssocGrpMemLimit`` behind 232 GB held by the account's other jobs. The plan
   records that headroom as ``room``; a FIRST round with ``room`` 0 is not submitted
   (:mod:`lab_commons.hpc.run`), so the part goes to a host that has it.

THE THROTTLE FOLLOWS THE HEADROOM WHILE THE ARRAY LIVES (:func:`live_throttle`). The ``%N`` an array is
submitted with is the headroom AT SUBMIT; as the account's other jobs end it is stale. Measured 2026-10-19
(Ada, 5 accounts of 96 cores): 22 single-core tasks ran (~5%) while runs reported ``STALLED ...
(JobArrayTaskLimit)`` -- pending on their OWN throttle. Each gather re-reads the account and raises (or
lowers) the throttle to what the grant's ceiling (GrpTRES cpu and mem, the QOS's per-user job limit, all read
from the cluster) has free, bounded by the tasks still to run.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Final

from lab_commons.hpc.allocation import session_room
from lab_commons.hpc.config import GIB, Cluster, Cost, Limits, Policy
from lab_commons.hpc.grants import COMMENT_PREFIX, Grant, Paging, Session
from lab_commons.hpc.region import free_mem_mb, may_place
from lab_commons.hpc.shards import one_wave
from lab_commons.hpc.slurm import Snapshot
from lab_commons.resources.dimensions import CPU, GPU, MEMORY
from lab_commons.width import fits

#: Slurm reports memory in MiB; :data:`~lab_commons.resources.dimensions.MEMORY` counts bytes.
MIB: Final = 1024**2

#: A partition's refusal when its QOS takes no more submits from the caller (:meth:`Quota.submit_room` is 0).
SUBMIT_ROOM_SPENT: Final = 'submit limit already reached'


class Unplaceable(ValueError):
    """No room: no headroom, quota, partition wall or inodes for the work -- never a malformed request.

    The one refusal a costed grant may answer (:func:`lab_commons.hpc.jobs.tiered`); any other ``ValueError``
    is a defect in the request and propagates as itself.
    """


__all__ = [
    'FILES_PER_RUN',
    'MIB',
    'SUBMIT_ROOM_SPENT',
    'Plan',
    'Unplaceable',
    'allocate',
    'free_slots',
    'grantable_gb',
    'headroom',
    'live_throttle',
    'make_plan',
    'plan_on',
    'quota_slots',
    'spend',
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
    #: The ``--comment`` every task carries -- the session tag :func:`plan_on` stamps.
    comment: str = ''
    #: The grant's compute-node set-up (``module load ...``), run before the job's own; :func:`allocate` stamps it.
    setup: tuple[str, ...] = ()
    #: Tasks of this cost the account's headroom holds NOW (:func:`quota_slots` of what is left); ``None`` unread.
    room: int | None = None
    #: The longest wall (minutes) any open candidate granted when planned; ``None`` when one is unbounded or unread.
    #: A shard cut at it cannot be given more (:func:`lab_commons.hpc.records.fold`).
    top_minutes: float | None = None

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


def _open(snapshot: Snapshot, cluster: Cluster) -> tuple[list[tuple[str, str, int | None, float | None]], list[str]]:
    """The candidates open to a submit now -- ``(name, qos, submit room, ceiling)`` -- and why the rest are not."""
    open_, refusals = [], []
    for name in cluster.partitions or tuple(snapshot.partitions):
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
            refusals.append(f'{name}: qos {qos!r} {SUBMIT_ROOM_SPENT}')
            continue
        open_.append((name, qos, room, _ceiling(snapshot, name, qos)))
    return open_, refusals


def _held(seconds: Sequence[float], policy: Policy, ceilings: Sequence[float | None]) -> list[float]:
    """Each item's padded seconds, never past the longest wall an open candidate grants.

    An estimate past every ceiling is a guess, never a need: a kill's floor is twice what the id ran
    (:meth:`~lab_commons.compute.learned.Measured.killed`) and the margin multiplies it again. Measured
    2026-10-26 (Ada): an id priced 48 h padded to a 4321-min shard that neither shortq (720) nor defq
    (2880) holds, and every motronics run stuck at "no grant can take this work". Held to the longest
    ceiling, the id gets the most any partition gives; past that its wall cuts it and its cell says so.
    """
    padded = [s * policy.safety for s in seconds]
    top = _top(ceilings)
    return padded if top is None else [min(p, top * 60) for p in padded]


def _top(ceilings: Iterable[float | None]) -> float | None:
    """The longest of these walls (minutes); ``None`` when there is none or one is unbounded."""
    walls = list(ceilings)
    return None if not walls or None in walls else max(c for c in walls if c is not None)


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
        raise Unplaceable(msg)
    open_, refusals = _open(snapshot, cluster)
    padded = _held(seconds or [cost.seconds] * n_items, policy, [o[3] for o in open_])
    options = []
    for name, qos, room, ceiling in open_:
        at_once = min(allowed, snapshot.quota.qos_max_jobs.get(qos) or allowed)
        shards, minutes = one_wave(padded, policy, room, at_once=at_once, ceiling=ceiling)
        if ceiling is not None and minutes > ceiling:
            refusals.append(f'{name}: a {minutes}-min shard exceeds its {ceiling:.0f}-min ceiling')
            continue
        options.append((name, qos, shards, minutes, ceiling))
    if not options:
        msg = f'no candidate partition can take this work: {"; ".join(refusals)}'
        raise Unplaceable(msg)
    _, qos, chosen, minutes, _ = next((o for o in options if free_slots(snapshot, (o[0],), cost) > 0), options[0])
    partitions = tuple(o[0] for o in options if o[1] == qos and (o[4] is None or o[4] >= minutes))
    shards = tuple(chosen)
    max_jobs = snapshot.quota.qos_max_jobs.get(qos)
    now = quota_slots(snapshot, cost, limits, remaining=True)
    throttle = min(len(shards), max(1, now), max_jobs or len(shards))
    free = free_slots(snapshot, partitions, cost)
    running = max(1, min(throttle, free))
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
        room=now,
        top_minutes=_top(o[3] for o in open_),
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


def live_throttle(snapshot: Snapshot, cost: Cost, *, running: int, unfinished: int, max_jobs: int | None = None) -> int:
    """The concurrency an array of tasks of *cost* may have NOW: its *running* tasks plus what the ceiling has free.

    Free is :func:`quota_slots` of what the account's running jobs leave (cpu, memory, gpu as the cluster
    reports them), never a fixed number; bounded by the *unfinished* tasks and the QOS's per-user job limit
    *max_jobs*; at least one. An account that reports no ceiling at all frees its schedulable CPUs.
    """
    try:
        free = quota_slots(snapshot, cost, Limits(), remaining=True)
    except ValueError:  # no ceiling at all: what the nodes have free
        free = headroom(snapshot) // max(1, cost.cpus)
    return max(1, min(unfinished, running + free, max_jobs or unfinished))


def headroom(snapshot: Snapshot) -> int:
    """CPUs still free on the account: ``quota - held``, a missing quota leaving the schedulable nodes' free CPUs.

    *held* is every CPU the caller's jobs on the account hold, tagged or not. No box's share is carved out in
    advance: admission fits each job against what is free now.
    """
    quota = snapshot.quota.cpus
    if quota is None:
        cap = float(sum(node.cpus for node in snapshot.nodes if node.schedulable))
    else:
        cap = quota - sum(held.cpus for held in snapshot.usage.values())
    return max(0, math.floor(cap))


#: The inodes a submitted array adds to the home, whatever its width: its run directory, manifest, script and
#: the one results file every task appends to (:mod:`lab_commons.hpc.worker`); a task's output stays node-local.
FILES_PER_RUN: Final = 4


def plan_on(
    grant: Grant,
    snapshot: Snapshot,
    n_items: int,
    cost: Cost,
    *,
    session: Session | None,
    policy: Policy,
    seconds: Sequence[float] | None = None,
) -> Plan:
    """*grant*'s plan for *n_items* on its headroom, stamped with the session tag and the grant's set-up.

    Refused (``ValueError``, naming the account and why) when the account has no CPU headroom for one item, no
    partition can hold the work, or the home has fewer free inodes than the array writes (:data:`FILES_PER_RUN`).
    """
    room = headroom(snapshot)
    if room < cost.cpus:
        held = ', '.join(f'{tag or "untagged"}={h.cpus:g}' for tag, h in sorted(snapshot.usage.items()))
        msg = (
            f'{grant.name}: headroom {room} CPU < {cost.cpus} per item '
            f'(quota {snapshot.quota.cpus}, held {held or "nothing"})'
        )
        raise Unplaceable(msg)
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
        msg = f'{grant.name}: {refused}'
        raise type(refused)(msg) from refused
    if snapshot.inodes_free is not None and snapshot.inodes_free < FILES_PER_RUN:
        msg = f'{grant.name}: the home has {snapshot.inodes_free} free inodes, the array writes {FILES_PER_RUN}'
        raise Unplaceable(msg)
    return replace(plan, comment=COMMENT_PREFIX + session.name if session else '', setup=grant.setup)


def allocate(
    n_items: int,
    cost: Cost,
    candidates: Sequence[tuple[Grant, Snapshot]],
    *,
    session: Session | None,
    policy: Policy,
    seconds: Sequence[float] | None = None,
    paging: Paging | None = None,
    rerun_safe: bool = False,
) -> tuple[Grant, Plan]:
    """The ONE grant with room whose plan finishes EARLIEST (:func:`plan_on`), held to *session*'s cap. Pure.

    A grant with ``room`` -- the account's headroom now, group memory included, and the session's
    (:func:`session_room`) -- beats one without; then the earlier makespan; a tie goes to the grant with MORE
    CPU headroom -- measured now, pending jobs included (:func:`headroom`) -- then to the higher ``priority``,
    then to the earlier grant in the file. Two grants on one cluster plan the same makespan for a small batch,
    so without the headroom key the first in the file took every run however loaded it was. A session past
    its cap gets a plan with ``room`` 0 and a throttle of 1: it WAITS, never refused. No grant that can plan
    raises, naming each account and why. Spreading one batch over SEVERAL grants is :func:`lab_commons.hpc.span.span`.

    With *paging* declared, an account :func:`may_place` refuses is no option, and the CPU-headroom tie-break
    becomes BEST FIT: the account with the least group memory free -- the fullest that holds the task.
    """
    snapshots = [snapshot for _, snapshot in candidates]
    capped = session_room(session, snapshots, cost)
    options: list[tuple[bool, float, float, int, int, Grant, Plan]] = []
    refusals, roomless = [], True
    for order, (grant, snapshot) in enumerate(candidates):
        try:
            plan = plan_on(
                grant,
                snapshot,
                n_items,
                cost,
                session=session,
                policy=policy,
                seconds=seconds,
            )
        except ValueError as refused:
            refusals.append(str(refused))
            roomless = roomless and isinstance(refused, Unplaceable)
            continue
        if paging is not None and not may_place(order, plan.minutes, cost, snapshots, paging, rerun_safe=rerun_safe):
            refusals.append(f'{grant.name}: the large-job region')
            continue
        if capped is not None:
            room = capped if plan.room is None else min(plan.room, capped)
            plan = replace(plan, room=room, throttle=min(plan.throttle, max(1, capped)))
        fit = -headroom(snapshot) if paging is None else free_mem_mb(snapshot)
        rank = (plan.room == 0, plan.makespan_minutes, fit, -grant.priority, order)
        options.append((*rank, grant, plan))
    if not options:
        msg = 'no grant can take this work: ' + '; '.join(refusals or ['this machine holds no grant'])
        raise (Unplaceable if refusals and roomless else ValueError)(msg)
    *_, grant, plan = min(options, key=lambda o: o[:5])
    return grant, plan


def spend(
    snapshots: Sequence[tuple[Grant, Snapshot]], grant: Grant, qos: str, tasks: int
) -> list[tuple[Grant, Snapshot]]:
    """*snapshots* with *tasks* more submits queued under *qos* on *grant*: what the next band of a round plans on."""

    def queued(snapshot: Snapshot) -> Snapshot:
        held = {**snapshot.quota.queued, qos: snapshot.quota.queued.get(qos, 0) + tasks}
        return replace(snapshot, quota=replace(snapshot.quota, queued=held))

    return [(g, queued(s) if g == grant else s) for g, s in snapshots]
