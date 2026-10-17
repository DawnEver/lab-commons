"""Spread ONE batch of items over EVERY grant that makes it finish sooner -- each slice sized to its grant. Pure.

A GRANT'S SLICE IS ITS SHARE OF THE THROUGHPUT. Each grant that can take the work at all is planned on the
WHOLE batch (:func:`lab_commons.hpc.plan.plan_on`: its headroom, its queue's free slots, its quota and
its home's free inodes); the padded seconds over that plan's makespan is the grant's throughput, and the
items are cut into consecutive slices whose seconds are proportional to it, so every grant's slice ends at
about the same time. Each slice is then planned on its own grant.

ONE GRANT ONLY WHEN THAT IS FASTEST. The spread is kept only when every slice holds at least one shortest
shard (``policy.shard_minutes_min`` of padded time -- less is start-up a grant is not worth) and its
slowest slice finishes strictly before the best single grant's plan (:func:`lab_commons.hpc.plan.allocate`,
whose tie rules stand) and the slices together start no more tasks at once than the session cap allows;
otherwise the batch goes to that one grant. Nothing is reserved on any grant:
each plan is Slurm's to queue, and a grant that later dies hands its slice back to
:func:`span` over the grants still answering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import accumulate

from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.grants import Grant, Session
from lab_commons.hpc.plan import Plan, allocate, plan_on, session_room
from lab_commons.hpc.slurm import Snapshot

__all__ = ['Placement', 'span', 'split']


@dataclass(frozen=True)
class Placement:
    """One grant's slice of a batch: the items ``[start, stop)`` and the plan that runs them there."""

    grant: Grant
    plan: Plan
    items: tuple[int, int]


def split(seconds: Sequence[float], weights: Sequence[float]) -> list[tuple[int, int]]:
    """Consecutive ``[start, stop)`` ranges covering *seconds*, each holding about its *weights* share of the sum.

    A cut falls at the item whose running sum is nearest the target, so a range may be empty.
    """
    total, whole = sum(seconds), sum(weights)
    running = [0.0, *accumulate(seconds)]
    cuts, start = [], 0
    for share in list(accumulate(weights))[:-1]:
        target = total * share / whole
        stop = min(range(start, len(seconds) + 1), key=lambda i: abs(running[i] - target))
        cuts.append((start, stop))
        start = stop
    return [*cuts, (start, len(seconds))]


def span(
    n_items: int,
    cost: Cost,
    candidates: Sequence[tuple[Grant, Snapshot]],
    *,
    session: Session | None,
    policy: Policy,
    seconds: Sequence[float] | None = None,
) -> tuple[Placement, ...]:
    """Where *n_items* run: every useful grant's slice, or the one fastest grant -- see the module docstring.

    Raises ``ValueError`` naming every account and why when no grant can take the work.
    """
    each = list(seconds) if seconds is not None else [cost.seconds] * n_items
    grant, plan = allocate(n_items, cost, candidates, session=session, policy=policy, seconds=seconds)
    alone = (Placement(grant, plan, (0, n_items)),)
    able: list[tuple[Grant, Snapshot, float]] = []
    for candidate, snapshot in candidates:
        try:
            whole = plan_on(candidate, snapshot, n_items, cost, session=session, policy=policy, seconds=seconds)
        except ValueError:
            continue
        able.append((candidate, snapshot, sum(each) / whole.makespan_minutes))
    if len(able) <= 1:  # a spread needs two grants
        return alone
    placements = []
    for (candidate, snapshot, _), (start, stop) in zip(able, split(each, [r for *_, r in able]), strict=True):
        if start == stop:
            continue
        if sum(each[start:stop]) * policy.safety < policy.shard_minutes_min * 60:
            return alone
        try:
            part = plan_on(
                candidate,
                snapshot,
                stop - start,
                cost,
                session=session,
                policy=policy,
                seconds=None if seconds is None else each[start:stop],
            )
        except ValueError:
            return alone
        placements.append(Placement(candidate, part, (start, stop)))
    capped = session_room(session, [snapshot for _, snapshot in candidates], cost)
    if capped is not None and sum(p.plan.throttle for p in placements) > capped:
        return alone  # the session cap counts the whole pool; one array holds it
    spread = max(p.plan.makespan_minutes for p in placements)
    return tuple(placements) if spread < plan.makespan_minutes else alone
