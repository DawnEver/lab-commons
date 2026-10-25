"""Cut items into SHARDS -- consecutive ranges an array task runs back to back -- and size the wave. Pure.

A shard holds enough padded time to amortise start-up (``shard_minutes_min``) without passing
``shard_minutes_max``; both bounds grow until the array fits what runs at once, so one wave is the
makespan (:func:`one_wave`). :func:`balance` refills the same number of shards longest-processing-time first.
The plan that carries them is :func:`lab_commons.hpc.plan.make_plan`.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence

from lab_commons.hpc.config import Policy

__all__ = ['balance', 'one_wave']


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


def one_wave(
    padded: Sequence[float],
    policy: Policy,
    room: int | None,
    *,
    at_once: int,
    ceiling: float | None,
) -> tuple[list[tuple[int, int]], int]:
    """Shards no more than run *at_once*, so the makespan IS the wall; more only when *ceiling* forces it.

    Measured 2026-10-10: a round planned 24 shards of 21 min for 17 concurrent tasks -- a 42-min makespan
    under a 21-min wall, so every task was killed at half the time the round itself expected to take.
    A second wave buys nothing a longer wall does not, and the longer wall is what a slow id needs.
    """
    cap = at_once if room is None else min(room, at_once)
    shards = _shards(padded, policy, cap)
    if ceiling is not None and _wall(padded, shards) > ceiling:
        shards = _shards(padded, policy, room)
    return shards, _wall(padded, shards)
