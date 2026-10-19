"""Where a remote run is -- queued with Slurm's reason, running k of n, done -- and when a pend is a STALL.

Read-only over the run's state on the cluster (:mod:`lab_commons.hpc.run`) plus one ``squeue`` of its
current job; a stall is judged against a fresh probe of every grant, never guessed from a wait alone.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

from lab_commons.hpc.cluster import Runner, probe
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import free_slots
from lab_commons.hpc.run import load_state, shares_home

__all__ = ['Progress', 'held_by_a_limit', 'parse_queue', 'where']

#: One round trip: the cluster's clock, then each task of the job -- state, Slurm's reason, the partitions
#: it may start in, and when it was submitted. Both times are the cluster's clock, so no timezone is mixed.
_QUEUE: Final = 'date +%Y-%m-%dT%H:%M:%S; squeue -h -r -j {job} -o "%T|%r|%P|%V"'
#: squeue's refusal of a job id it has aged out: the job left the queue long ago, so the run is finished.
_FORGOTTEN: Final = 'Invalid job id specified'
#: Slurm's pend reasons for an ACCOUNT or QOS resource limit (``AssocGrpMemLimit``, ``QOSMaxCpuPerUserLimit``,
#: ...): the job waits on what the account's other jobs hold, not on the cluster. ``JobArrayTaskLimit`` is
#: the array's own throttle, so it never matches.
_LIMIT: Final = re.compile(r'^(?:Assoc|QOS)(?:Grp|Max)\w+$')


@dataclass(frozen=True)
class Progress:
    """Where one run is: its current job's tasks queued, running, done -- and a stall, when there is one."""

    sha: str
    job_id: str
    shards: int
    pending: int = 0
    running: int = 0
    reasons: tuple[str, ...] = ()
    partitions: tuple[str, ...] = ()
    pending_minutes: float = 0.0
    finished: bool = False
    stall: str = ''

    @property
    def done(self) -> int:
        """Tasks no longer in the queue -- ended, however they ended."""
        return self.shards - self.pending - self.running

    def describe(self) -> str:
        """One line a human reads; a stall adds a second, with its remedy."""
        head = f'{self.sha[:12]} job {self.job_id}: '
        if self.finished:
            return head + 'finished; its record is ready'
        parts = [f'running {self.running} of {self.shards}', f'done {self.done}']
        if self.pending:
            why = ', '.join(self.reasons) or 'no reason given'
            on = ','.join(self.partitions)
            parts.insert(0, f'queued {self.pending} for {self.pending_minutes:.0f} min ({why}) on {on}')
        return head + '; '.join(parts) + (f'\n  STALLED: {self.stall}' if self.stall else '')


def parse_queue(text: str) -> tuple[int, int, tuple[str, ...], tuple[str, ...], float]:
    """:data:`_QUEUE`'s output: pending, running, pending reasons, their partitions, the oldest pend in minutes."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0, 0, (), (), 0.0
    now = datetime.fromisoformat(lines[0])
    pending = running = 0
    reasons: dict[str, None] = {}
    partitions: dict[str, None] = {}
    oldest = now
    for line in lines[1:]:
        state, reason, parts, submitted = [*line.split('|'), '', '', ''][:4]
        if state == 'RUNNING':
            running += 1
        elif state == 'PENDING':
            pending += 1
            reasons[reason] = None
            partitions.update(dict.fromkeys(filter(None, parts.split(','))))
            with contextlib.suppress(ValueError):
                oldest = min(oldest, datetime.fromisoformat(submitted))
    return pending, running, tuple(reasons), tuple(partitions), (now - oldest).total_seconds() / 60


def _stall(
    progress: Progress,
    state: dict[str, Any],
    machine: Machine,
    runners: dict[str, Runner],
    policy: Policy,
) -> str:
    """Why a pend past ``policy.stall_minutes`` is a stall, and its remedy -- ``''`` when nothing is idle.

    A stall is DATA: a declared partition (of any grant) with a free slot for one task of this round right
    now. Idle capacity in a partition the job does not name is fixed by naming it; idle in one it already
    names means Slurm holds the job for a reason that is not capacity (priority, a QOS limit), and that
    reason is quoted. A grant of another user can take the run only after a build in that user's home.
    """
    if not progress.pending or progress.pending_minutes < policy.stall_minutes:
        return ''
    plan = state['rounds'][-1]
    cost = Cost(cpus=plan['cpus'], mem_gb=plan['mem_mb'] / 1024, gpus=plan['gpus'])
    home = next(g for g in machine.grants if g.account == tuple(state['current']['account']))
    here: list[str] = []
    elsewhere: list[str] = []
    for grant in machine.grants:
        snapshot = probe(runners[grant.account], grant.slurm_account)
        for name in grant.partitions or tuple(snapshot.partitions):
            if free := free_slots(snapshot, (name,), cost):
                (here if shares_home(grant, home) else elsewhere).append(f'{name} on {grant.name}: {free} free')
    if not here and not elsewhere:
        return ''
    said = f'pending {progress.pending_minutes:.0f} min while {"; ".join(here + elsewhere)}. '
    if any(p not in progress.partitions for p in home.partitions):
        every = ','.join(home.partitions)
        return said + (
            f'Remedy: requeue across every declared partition: scontrol update JobId={progress.job_id} '
            f'Partition={every}'
        )
    remedy = (
        f'The job already names every declared partition, so Slurm holds it for {", ".join(progress.reasons)}, '
        'not for capacity'
    )
    if elsewhere:
        remedy += '; another grant has room but its home holds no build of this tree -- run submit from it'
    return said + remedy


def held_by_a_limit(progress: Progress, policy: Policy) -> str:
    """Why a job with NOTHING running has pended past ``policy.stall_minutes`` on an account limit; else ``''``.

    Such a claim is idle on a host whose account cannot run it, while another host's could: the forge
    releases it (:mod:`lab_commons.dev.forgeslurm`). Measured 2026-10-16: a 52-task array sat an hour on
    ``AssocGrpMemLimit`` with 0 running while other accounts were nearly idle. A job with a task running is
    progressing, and a pend for any other reason (priority, resources) is the cluster's, not the account's.
    """
    if progress.running or not progress.pending or progress.pending_minutes < policy.stall_minutes:
        return ''
    if not progress.reasons or not all(_LIMIT.match(reason) for reason in progress.reasons):
        return ''
    return (
        f'job {progress.job_id} pended {progress.pending_minutes:.0f} min on {", ".join(progress.reasons)} '
        "with nothing running: the account's own jobs hold its limit; released for a host with headroom"
    )


def where(sha: str, part: str, machine: Machine, connect: Callable[[Grant], Runner], *, policy: Policy) -> Progress:
    """Where *sha*'s run of *part* is now -- see :class:`Progress`. Reads; submits nothing."""
    runners = {g.account: connect(g) for g in machine.grants}
    state, home = load_state(sha, part, machine, runners)
    if 'record' in state:
        last = state['rounds'][-1]
        return Progress(sha=sha, job_id=','.join(last['job_ids']), shards=len(last['shards']), finished=True)
    current = state['current']
    job_id = ','.join(current['job_ids'])  # one array per memory class of the round (lab_commons.hpc.run)
    try:
        queue = home(_QUEUE.format(job=job_id), None)
    except RuntimeError as exc:
        if _FORGOTTEN not in str(exc):
            raise
        return Progress(sha=sha, job_id=job_id, shards=current['shards'], finished=True)
    pending, running, reasons, partitions, minutes = parse_queue(queue)
    progress = Progress(
        sha=sha,
        job_id=job_id,
        shards=current['shards'],
        pending=pending,
        running=running,
        reasons=reasons,
        partitions=partitions,
        pending_minutes=minutes,
    )
    return replace(progress, stall=_stall(progress, state, machine, runners, policy))
