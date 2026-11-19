"""A JOB MAY NAME ITS GRANTS: ``[policy] grants`` restricts one job's placement to named accounts (#191).

A research session may be told which accounts a kind of work uses (CPU work on one account, GPU work on any).
The machine's reachable grants are one pool to the planner; a job file narrows it for that job alone, so a per-kind
rule needs no hand-filtered copy of the machine file.

A name is the grant's ``user`` or its account file's ``<cluster>-<user>``. :func:`restricted` keeps the reachable
grants a name matches. A name matching no declared grant, or only grants this machine does not reach, is refused
by name -- never silently dropped -- and an empty ``grants`` keeps the whole pool.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from lab_commons.hpc.grants import Grant, Machine

__all__ = ['restricted']


def _names(grant: Grant) -> frozenset[str]:
    return frozenset({grant.user, f'{grant.cluster_name}-{grant.user}'} if grant.cluster_name else {grant.user})


def restricted(machine: Machine, names: Sequence[str]) -> Machine:
    """*machine* with only the reachable grants *names* match; *names* empty: *machine* unchanged.

    Raises:
        ValueError: a name matches no declared grant, or one that matches only grants this machine does not reach.

    """
    if not names:
        return machine
    unknown = [name for name in names if not any(name in _names(one) for one in (*machine.grants, *machine.refused))]
    unreached = [
        name for name in names if name not in unknown and not any(name in _names(one) for one in machine.grants)
    ]
    if unknown or unreached:
        reachable = sorted({one.user for one in machine.grants})
        parts = [
            f'{unknown} name no declared grant' if unknown else '',
            f'{unreached} are not reached here' if unreached else '',
        ]
        msg = f'[policy] grants: {"; ".join(part for part in parts if part)} -- this machine reaches {reachable}'
        raise ValueError(msg)
    return replace(machine, grants=tuple(one for one in machine.grants if _names(one) & set(names)))
