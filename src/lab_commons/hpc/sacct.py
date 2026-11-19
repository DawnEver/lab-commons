"""Slurm's ACCOUNTING of a submitted array (``sacct``): each shard's latest clock and state.

A resubmitted array supersedes an earlier one shard by shard; a task that has not started is listed as a
range (``[0-3,7%4]``) and expanded here. Read by the gather (:mod:`lab_commons.hpc.gather`) and the job
views (:mod:`lab_commons.hpc.jobs`); nothing here submits or writes.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.phases import Clock

__all__ = ['job_states', 'shard_clock', 'shard_states']

#: The ``sacct`` columns of a :class:`~lab_commons.hpc.phases.Clock`, in its field order.
_CLOCK_FIELDS: Final = 'JobID,State,Submit,Start,End,ElapsedRaw,AllocCPUS'

_TASK: Final = re.compile(r'^(\d+)_(\d+|\[[^\]]*\])$')


def _expand(spec: str) -> list[int]:
    """``[0-3,7%4]`` into ``[0, 1, 2, 3, 7]`` -- how ``sacct`` lists tasks that have not started."""
    indices = []
    for part in spec.strip('[]').split('%')[0].split(','):
        low, _, high = part.partition('-')
        indices.extend(range(int(low), int(high or low) + 1))
    return indices


def _epoch(text: str) -> float | None:
    """``sacct``'s local ``2026-10-11T02:36:16`` as epoch seconds; ``Unknown``/``None``/empty is ``None``."""
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _job_clocks(run: Runner, job_ids: Sequence[str]) -> dict[str, dict[int, Clock]]:
    """Each job id's shards and their clocks, from ONE ``sacct`` call over every id (one ssh, however many jobs)."""
    clocks: dict[str, dict[int, Clock]] = {job_id: {} for job_id in job_ids}
    if not job_ids:
        return clocks
    ids = ','.join(shlex.quote(job_id) for job_id in job_ids)
    out = run(f'sacct -n -P -X -j {ids} -o {_CLOCK_FIELDS}', None)
    for line in out.splitlines():
        task, *rest = line.split('|')
        match = _TASK.match(task.strip())
        if not match or match.group(1) not in clocks:
            continue
        state, submit, start, end, elapsed, cpus = (*rest, '', '', '', '', '', '')[:6]
        clock = Clock(
            state=state.split()[0] if state.strip() else 'UNKNOWN',
            submit=_epoch(submit),
            start=_epoch(start),
            end=_epoch(end),
            elapsed=float(elapsed or 0),
            cpus=int(cpus or 1),
        )
        index = match.group(2)
        for shard in _expand(index) if index.startswith('[') else [int(index)]:
            clocks[match.group(1)][shard] = clock
    return clocks


def shard_clock(run: Runner, job_ids: Sequence[str]) -> dict[int, Clock]:
    """Each shard's latest :class:`Clock` across one or more submissions (a resubmit supersedes)."""
    merged: dict[int, Clock] = {}
    for per_job in _job_clocks(run, job_ids).values():  # in job_ids order: a later submission wins
        merged.update(per_job)
    return merged


def shard_states(run: Runner, job_ids: Sequence[str]) -> dict[int, str]:
    """Each shard's latest Slurm state -- the state column of :func:`shard_clock`."""
    return {shard: clock.state for shard, clock in shard_clock(run, job_ids).items()}


def job_states(run: Runner, job_ids: Sequence[str]) -> dict[str, dict[int, str]]:
    """Each job id's shards by Slurm state, all ids read in ONE ``sacct`` call (``status`` over many jobs)."""
    return {job_id: {s: c.state for s, c in shards.items()} for job_id, shards in _job_clocks(run, job_ids).items()}
