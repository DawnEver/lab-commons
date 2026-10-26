"""THE SLURM ACCOUNT'S PROVIDER (:class:`~lab_commons.resources.Provider`): what an account can take NOW.

``cpu`` AND ``mem_gb`` over EVERY grant of the account, ``submits`` -- the job submissions its QOS still accepts
(infinite when uncapped) -- ``files``, what the home's FILE quota still takes (user ruling 2026-10-14: an Ada home
at its 2,020,000-file limit failed every write while its supply read free cores), and ``linux``, counted as
``cpu``. Its tool seats are the box's one seat provider's (:class:`lab_commons.box.providers.ToolSeats`) on the
account's scope, read where this runs: on the account's own login node.

An account NOT MEASURED supplies nothing (:func:`unmeasured`): every part it is asked for is refused naming what it
lacks, never admitted against a guess.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Final

from lab_commons.compute.model import Resources, Supply
from lab_commons.hpc.plan import headroom
from lab_commons.hpc.slurm import Snapshot
from lab_commons.resources.model import Actual, Claim

__all__ = ['NAMES', 'SlurmAccount', 'unmeasured']

#: Every name a Slurm account supplies.
NAMES: Final = frozenset({'cpu', 'mem_gb', 'submits', 'files', 'linux'})


def unmeasured(scope: str) -> Supply:
    """An account whose Slurm did not answer: nothing, so nothing is admitted on it."""
    return Supply(scope, Resources())


@contextmanager
def _allocated() -> Iterator[Actual]:
    """A task is held to its claim by its own allocation (``--time``, ``--mem``), which Slurm enforces."""
    yield Actual()


@dataclass(frozen=True)
class SlurmAccount:
    """An account over EVERY grant: ``(snapshot, qos)`` each, summed; *files* is the home's file headroom.

    ``cpu`` per grant is :func:`lab_commons.hpc.plan.headroom` (quota less what is held); ``mem_gb`` the quota's
    remaining memory less every pending task's, else -- uncapped -- the free memory of the schedulable nodes. At
    the file limit *files* is 0 and every run -- each creates files -- is refused naming ``files``; infinite only
    where no file quota exists.
    """

    snapshots: Sequence[tuple[Snapshot, str]]
    files: float = math.inf
    names: frozenset[str] = NAMES

    def measure(self, scope: str) -> Supply:
        """The account now, from its snapshots."""
        cpu = mem = submits = 0.0
        for snapshot, qos in self.snapshots:
            cpu += headroom(snapshot)
            left = snapshot.quota.remaining()['mem']
            if left is not None:  # the ceiling less running, less what pends: a pending task is held, as its cpus are
                left = max(0.0, left - sum(task.mem_mb for task in snapshot.pending))
            mem += (left if left is not None else sum(n.mem_mb for n in snapshot.nodes if n.schedulable)) / 1024
            room = snapshot.quota.submit_room(qos)
            submits += math.inf if room is None else room
        measured = Resources({'cpu': cpu, 'mem_gb': mem, 'submits': submits, 'files': self.files, 'linux': cpu})
        return Supply(scope, measured, NAMES)

    def enforce(self, scope: str, claim: Claim) -> AbstractContextManager[Actual]:
        """See :func:`_allocated`."""
        del scope, claim
        return _allocated()
