"""The large-job REGION of paged placement: which account it is, and what may run there. Pure.

PAGING IS OS MEMORY MANAGEMENT OVER THE POOL (user design 2026-10-17, :class:`lab_commons.hpc.grants.Paging`).
The emptiest account is set apart for large tasks; it rotates as load moves, a waiting large task pins it, and
it is DRAINED -- never killed -- by admitting only small movable tasks while a large one waits.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from lab_commons.hpc.config import Cost
from lab_commons.hpc.grants import Paging
from lab_commons.hpc.slurm import Snapshot

__all__ = ['free_mem_mb', 'may_place', 'region']


def free_mem_mb(snapshot: Snapshot) -> float:
    """The account's group-memory headroom: ``GrpTRES`` mem less what runs; no ceiling is unbounded."""
    left = snapshot.quota.remaining()['mem']
    return math.inf if left is None else left


def _waits_large(snapshot: Snapshot, paging: Paging) -> bool:
    return any(paging.pages(held.cpus, held.mem_mb / 1024) > paging.large_pages for held in snapshot.pending)


def region(snapshots: Sequence[Snapshot], paging: Paging) -> int | None:
    """The large-job region's index: where a large task WAITS, else the emptiest account; ``None`` for one account.

    Emptiest is the most group memory free, the earlier account on a tie, so the region rotates as load moves
    and belongs to no session. A waiting large task pins it, so it keeps draining until that task starts.
    """
    if len(snapshots) <= 1:  # a region is one account set apart from the rest
        return None
    waiting = [i for i, snapshot in enumerate(snapshots) if _waits_large(snapshot, paging)]
    if waiting:
        return waiting[0]
    return max(range(len(snapshots)), key=lambda i: (free_mem_mb(snapshots[i]), -i))


def may_place(
    index: int, minutes: float, cost: Cost, snapshots: Sequence[Snapshot], paging: Paging, *, rerun_safe: bool
) -> bool:
    """Whether a plan of wall *minutes* may run on account *index*: large only in the region, else not large.

    The region takes a SMALL task that is MOVABLE -- rerun-safe and its wall within ``movable_minutes`` -- and
    only while no large task waits there: drain, never kill.
    """
    where = region(snapshots, paging)
    if where is None:
        return True
    size = paging.size(cost)
    if size == 'large':
        return index == where
    if index != where:
        return True
    movable = rerun_safe and minutes <= paging.movable_minutes
    return size == 'small' and movable and not _waits_large(snapshots[where], paging)
