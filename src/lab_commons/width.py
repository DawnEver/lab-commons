"""Tier 1 -- the one width formula: how many copies of a demand fit in a capacity.

ONE QUESTION, ONE ANSWER. A cluster node's free cores and memory and a scheduler quota
(:mod:`lab_commons.hpc.plan`) ask the same thing of the same shape: the smallest ``capacity // demand``
over the dimensions both sides name. (A box no longer sizes a pool here: what runs on a box is a claim
the one ledger admits, :mod:`lab_commons.box`.)

KEYED BY :data:`lab_commons.resources.dimensions.DIMENSIONS`, in their units (memory in bytes). Memory-first is not a
special case: memory
is a dimension like any other, and ``min`` is what makes the tightest one win.

Pure arithmetic that needs nothing but the dimension names.
"""

from __future__ import annotations

from collections.abc import Mapping

from lab_commons.resources.dimensions import DIMENSIONS

__all__ = ['fits']


def fits(capacity: Mapping[str, float | None], demand: Mapping[str, float]) -> int:
    """How many copies of *demand* fit in *capacity*.

    A dimension the demand does not use (zero or absent) does not constrain; a capacity of ``None`` is
    "no ceiling on this dimension". A demand on a dimension the capacity does not mention fits zero
    times, because an unread capacity is not an unlimited one.

    Raises:
        ValueError: a demand names a dimension :data:`~lab_commons.resources.dimensions.DIMENSIONS` does not, or
            no dimension constrains it at all (the width would be unbounded).

    """
    unknown = set(demand) - set(DIMENSIONS)
    if unknown:
        msg = f'demand names dimensions not in DIMENSIONS: {sorted(unknown)}'
        raise ValueError(msg)
    counts = []
    for name, need in demand.items():
        if need <= 0:
            continue
        if name not in capacity:
            return 0
        have = capacity[name]
        if have is not None:
            counts.append(max(0, int(have // need)))
    if not counts:
        msg = 'no dimension constrains this demand -- the width would be unbounded'
        raise ValueError(msg)
    return min(counts)
