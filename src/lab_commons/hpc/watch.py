"""Settle outstanding remote runs: gather each, optionally wait, write records, say where the rest are.

THE ONE CODE PATH of ``run gather [--wait]`` and ``run watch``; ``run status`` reads the same list
(:mod:`lab_commons.hpc.outstanding`) and the same :func:`lab_commons.hpc.progress.where`. A written record
takes its run off the list. Notification is NOT here: a harness tick runs ``run watch`` and reads its exit.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from lab_commons.hpc import outstanding
from lab_commons.hpc.cluster import Runner, Unreachable
from lab_commons.hpc.config import Config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.progress import where
from lab_commons.hpc.records import summary, write_record
from lab_commons.hpc.run import gather_run
from lab_commons.log import emit

__all__ = ['PENDING', 'RED', 'settle', 'status']

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
    sleep: Callable[[float], object] | None,
) -> int:
    """Gather each of *shas*; with a *sleep*, poll every ``policy.poll_seconds`` until all have a record.

    Exit: 0 every record green, 1 any red (or a run that cannot be gathered), :data:`PENDING` when not
    waiting and some run is still on the cluster.
    """
    left, red = list(shas), False
    while True:
        for sha in list(left):
            try:
                record = gather_run(sha, machine, connect, cost=config.cost, policy=config.policy)
                if record is None:
                    emit(where(sha, machine, connect, policy=config.policy).describe())
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
            outstanding.remove(sha)
            left.remove(sha)
            red = red or not RED.isdisjoint(record['outcomes'].values())
            emit(
                f'{sha[:12]} on {record["cluster"]} ({record["system"]}, python {record["python"]}): {summary(record)}'
            )
            emit(f'written {path}')
            emit(f'record it as the {record["platform"]} part: python -m lab_commons.dev.platformparts record {path}')
        if not left:
            return 1 if red else 0
        if sleep is None:
            return PENDING
        sleep(config.policy.poll_seconds)


def status(machine: Machine, connect: Callable[[Grant], Runner], config: Config) -> int:
    """Where every outstanding run is, one line each (two when it stalls)."""
    entries = outstanding.outstanding()
    if not entries:
        emit(f'no outstanding run ({outstanding.directory()} is empty)')
    for entry in entries:
        try:
            emit(where(entry['sha'], machine, connect, policy=config.policy).describe())
        except Unreachable as exc:
            emit(f'{entry["sha"][:12]}: unreachable: {exc}')
    return 0
