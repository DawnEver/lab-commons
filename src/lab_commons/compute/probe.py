"""THE PROBES -- what a host or a Slurm account has NOW, read from the system and returned as a :class:`Supply`.

Three sources, one value (:mod:`lab_commons.compute.model`):

* **box**: what the box ledger can still admit (:mod:`lab_commons.box`, the one source of a box's resources):
  its MEASURED room (:func:`lab_commons.box.supply.room`: free now, less what every claim it holds -- an
  interactive verify, a producer, anything run through :func:`lab_commons.box.run` -- has not taken yet,
  less the ``[reserve]`` margins, within its :class:`~lab_commons.box.Capacity`), its tool seats, and the
  OS itself (``windows``, ``linux``, ``macos``) with count = ``cpu``.
* **tools**: PRESENCE IS PROBED, NOT DECLARED. A tool with a check is present exactly when
  :mod:`lab_commons.compute.presence` says so -- found, at the family's declared version, its smoke passed,
  no real unit failed on it since -- declared or not, and a declaration never makes it present. The machine
  file's ``[capabilities.<tool>]`` ``seats = N`` is the one number a person declares, because no probe can
  read a licence; a present tool nobody declared has 1. A tool without a check is present when declared. Box
  and Slurm account alike take their tools through the one path, :func:`seated`.
* **slurm account**: ``cpu`` AND ``mem_gb`` over EVERY grant of the account (:func:`account`), plus ``submits``
  -- the job submissions its QOS still accepts (infinite when uncapped) -- and ``files``, what the home's
  FILE quota still takes (user ruling 2026-10-14: an Ada home at its 2,020,000-file limit failed every
  write while its supply read free cores; the caller reads it, :mod:`dev_lab.dev.diskusage`).

The readers of the OS are module functions taken as defaults, so a test passes fakes; the arithmetic is pure.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from lab_commons.box import Capacity, Holding
from lab_commons.box.ledger import Read
from lab_commons.box.supply import Reading, room
from lab_commons.compute.model import Resources, Supply
from lab_commons.compute.presence import Evidence, Presence, cached
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.hpc.plan import headroom
from lab_commons.hpc.slurm import Snapshot

__all__ = ['OS_NAMES', 'account', 'box', 'inventory', 'os_name', 'seated', 'tools']

#: ``sys.platform`` -> the resource name of the OS.
OS_NAMES: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}


def os_name(platform: str = sys.platform) -> str:
    """The OS resource of *platform*; an OS the family does not serve is refused by name."""
    if platform not in OS_NAMES:
        msg = f'no OS resource for sys.platform {platform!r}; known: {sorted(OS_NAMES)}'
        raise ValueError(msg)
    return OS_NAMES[platform]


# --- tools --------------------------------------------------------------------------------------------


def inventory(seats: Mapping[str, int], presence: Mapping[str, Presence] | None = None) -> dict[str, Presence]:
    """Every tool of this host with its verdict, the one source of :func:`seated` and of the host card.

    Each checked tool is as *presence* read it (default: the cached reading); each declared resource
    without a check is present, its evidence the declaration.
    """
    presence = cached() if presence is None else presence
    unchecked = {
        name: Presence(name, present=True, evidence=(Evidence('declared, no check', f'seats={count}', ''),))
        for name, count in seats.items()
        if name not in CHECKS
    }
    return {**presence, **unchecked}


def seated(
    seats: Mapping[str, int],
    *,
    held: Iterable[Mapping[str, int]] = (),
    presence: Mapping[str, Presence] | None = None,
) -> tuple[Resources, frozenset[str]]:
    """THE ONE PATH to a host's tools: ``(counts, present)``, each its *seats* (1 when undeclared) less *held*.

    A tool with a check (:data:`~lab_commons.compute.toolchecks.CHECKS`) is present exactly when its
    *presence* says so (default: this host's cached reading, :func:`~lab_commons.compute.presence.cached`),
    whatever *seats* says; one without a check is present when *seats* declares it.
    """
    claims = list(held)
    present = frozenset(name for name, one in inventory(seats, presence).items() if one.present)
    counts = {name: max(0, seats.get(name, 1) - sum(one.get(name, 0) for one in claims)) for name in present}
    return Resources(counts), present


def tools(
    capabilities: Mapping[str, Mapping[str, Any]],
    *,
    running: Iterable[frozenset[str]] = (),
    presence: Mapping[str, Presence] | None = None,
) -> tuple[Resources, frozenset[str]]:
    """:func:`seated` over ``[capabilities]`` rows (``seats`` default 1), one seat per *running* need-set."""
    seats = {name: int(facts.get('seats', 1)) for name, facts in capabilities.items()}
    return seated(seats, held=[dict.fromkeys(needs, 1) for needs in running], presence=presence)


# --- the two kinds of supply ------------------------------------------------------------------------------


def box(
    scope: str,
    *,
    capacity: Capacity,
    holdings: Sequence[Holding] = (),
    read: Read = Reading.now,
    platform: str = sys.platform,
    presence: Mapping[str, Presence] | None = None,
) -> Supply:
    """The box now: its measured room beside the claims in *holdings* (the box ledger), its OS and its tools.

    *read* is the OS reader (default: :meth:`~lab_commons.box.supply.Reading.now`); the room is
    :func:`~lab_commons.box.supply.room`, the formula the ledger itself admits by.
    """
    free = room(capacity, holdings, read([one.pid for one in holdings]))
    cpu, mem_gb = free['cpu'], free['mem_gb']
    system = os_name(platform)
    counts, present = seated(capacity.seats, held=[one.seats for one in holdings], presence=presence)
    measured = Resources({'cpu': cpu, 'mem_gb': mem_gb, system: cpu}) + counts
    return Supply(scope, measured, present | measured.names | {system})


def account(
    scope: str,
    snapshots: Sequence[tuple[Snapshot, str]],
    *,
    seats: Mapping[str, int],
    files: float = math.inf,
    presence: Mapping[str, Presence] | None = None,
) -> Supply:
    """A Slurm account over EVERY grant: ``(snapshot, qos)`` each, summed; ``submits`` infinite if uncapped.

    Its tools go through :func:`seated`, as a box's do: the reading is the one taken where this runs, on the
    account's own linux login node, so a tool not installed there is absent and one installed there is present.

    *files* is the home's file-quota headroom (limit less used): at the limit it is 0, and a run -- every
    run creates files -- is refused naming ``files``. Infinite only where no file quota exists.

    The account's OS, ``linux``, counts as many as ``cpu``.
    ``cpu`` per grant is :func:`lab_commons.hpc.plan.headroom` (quota less what is held); ``mem_gb`` the
    quota's remaining memory less every pending task's, else -- uncapped -- the free memory of the schedulable nodes.
    """
    cpu = mem = submits = 0.0
    for snapshot, qos in snapshots:
        cpu += headroom(snapshot)
        left = snapshot.quota.remaining()['mem']
        if left is not None:  # the ceiling less running, less what pends: a pending task is held, as its cpus are
            left = max(0.0, left - sum(task.mem_mb for task in snapshot.pending))
        mem += (left if left is not None else sum(n.mem_mb for n in snapshot.nodes if n.schedulable)) / 1024
        room = snapshot.quota.submit_room(qos)
        submits += math.inf if room is None else room
    linux = OS_NAMES['linux']  # a Slurm account runs on linux: the OS is a resource there like any other
    counts, present = seated(seats, presence=presence)
    measured = Resources({'cpu': cpu, 'mem_gb': mem, 'submits': submits, 'files': files, linux: cpu}) + counts
    return Supply(scope, measured, present | {'cpu', 'mem_gb', 'submits', 'files', linux})
