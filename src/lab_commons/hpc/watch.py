"""Settle remote runs: gather each, optionally wait, write records, say where the rest are.

THE ONE CODE PATH of ``run gather [--wait]`` and of ``forge serve``'s gather step; where a run still
pending is comes from :func:`lab_commons.hpc.progress.where`. Notification is NOT here: ``forge serve``
posts a run's progress on its claim and raises alarms (:mod:`lab_commons.dev.forgerun`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from lab_commons.hpc.cluster import Runner, Unreachable
from lab_commons.hpc.config import Config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.phases import describe, phases
from lab_commons.hpc.progress import where
from lab_commons.hpc.records import summary, write_record
from lab_commons.hpc.run import gather_run
from lab_commons.log import emit

__all__ = ['PENDING', 'RED', 'settle']

#: Exit status of a gather that wrote no record yet: still running, re-submitted, or unreachable.
PENDING: Final = 3

#: Outcomes that make a written record a FAILED run: the code failed, or the run could not say.
RED: Final = frozenset({'failed', 'error', 'lost', 'missing'})


def settle(
    shas: list[str],
    machine: Machine,
    connect: Callable[[Grant], Runner],
    config: Config,
    output: Path,
    *,
    part: str,
    sleep: Callable[[float], object] | None,
) -> int:
    """Gather *part* on each of *shas*; with a *sleep*, poll every ``policy.poll_seconds`` until all are done.

    Exit: 0 every record green, 1 any red (or a run that cannot be gathered), :data:`PENDING` when not
    waiting and some run is still on the cluster.
    """
    left, red = list(shas), False
    while True:
        for sha in list(left):
            try:
                record = gather_run(sha, part, machine, connect, cost=config.cost, policy=config.policy)
                if record is None:
                    emit(where(sha, part, machine, connect, policy=config.policy).describe())
                    continue
            except Unreachable as exc:
                emit(f'pending: {exc}')
                continue
            except RuntimeError as exc:
                emit(f'{sha[:12]} cannot be gathered: {exc}')
                left.remove(sha)
                red = True
                continue
            path = write_record(record, output)
            left.remove(sha)
            red = red or not RED.isdisjoint(record['outcomes'].values())
            emit(
                f'{sha[:12]} on {record["cluster"]} ({record["system"]}, python {record["python"]}): {summary(record)}'
            )
            emit(describe(phases(record)))
            emit(f'written {path}')
            emit(f'post it as the {record["platform"]} part: python -m lab_commons.dev.platformparts post {path}')
        if not left:
            return 1 if red else 0
        if sleep is None:
            return PENDING
        sleep(config.policy.poll_seconds)
