"""THE BOX'S SUPPLY IS MEASURED (D3, 2026-10-16): free now, less what admitted claims have not taken yet.

A box is shared -- an editor, a vendor GUI, another user's session -- and none of them declares a claim,
so the machine's size less the declared claims over-states the room exactly by what they use. The OS
already counts them, and every claim's RESIDENT part: memory available and cores idle NOW. What it cannot
see is a claim that has not grown to its declaration yet, and that rest is still the claim's. So:

    room = OS free now - sum over claims of (declared - in use now, floored at 0) - the [reserve] margin

per dimension, memory by resident bytes and cores by busy cores over a short sample. It is CAPPED at the
size less every declaration (:class:`~lab_commons.box.demand.Capacity` less the claims): a sample taken
before a claim showed its load never lends what a declaration already owns.

A TOOL SEAT IS SUPPLY LIKE A CORE (2026-10-25). Its size is its tool's supply as its own probe measures it
(:func:`lab_commons.box.demand.seat_supply`; the declared ``[capabilities]`` count when there is no probe,
1 when undeclared). A seat whose row declares ``in_use = { process = [images] }`` is also OBSERVED:
the running processes of those images are its seats in use now, so a solve a person starts outside the ledger
takes its seat. Then

    seats free = size - max(held by claims, observed in use) - [reserve] seat_margin

MAX, not sum: a claim's own solver is a process the table shows too. An observation that could not be read
(:attr:`Reading.unseen`), or a supply probe that could not say (:attr:`Capacity.blind`), lends none of its seat,
naming why.

:func:`room` is the one formula, read by the ledger (:func:`lab_commons.box.ledger.hold`) and by the
forge's box supply (:func:`lab_commons.compute.probe.box`); :meth:`Reading.now` is the one OS reader.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from lab_commons.box.demand import GB, Capacity, Holding
from lab_commons.box.machine import provided, watched
from lab_commons.compute.model import Resources
from lab_commons.config import section
from lab_commons.proc import _process_rows, process_tree, system_memory, working_set_bytes
from lab_commons.proccpu import process_times, system_cpu_seconds

__all__ = ['SAMPLE_S', 'Reading', 'Usage', 'observe', 'room']

#: Seconds between the two cpu readings that make "busy now": long enough to average a scheduler tick.
SAMPLE_S: Final = 0.25


@dataclass(frozen=True)
class Usage:
    """What one claim's process tree holds now: resident bytes and busy cores."""

    resident_bytes: int
    busy_cores: float


@dataclass(frozen=True)
class Reading:
    """The OS now: cores and those idle, total and available memory, each claim pid's :class:`Usage`.

    ``seats_in_use`` is each watched seat's observed count; ``unseen`` names each one that could not be
    observed, and why.
    """

    cores: float
    idle_cores: float
    total_bytes: int
    available_bytes: int
    usage: Mapping[int, Usage] = field(default_factory=dict)
    seats_in_use: Mapping[str, int] = field(default_factory=dict)
    unseen: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def now(cls, pids: Iterable[int], *, sample_s: float = SAMPLE_S) -> Reading:
        """Read this machine: two cpu readings *sample_s* apart, memory, the trees of *pids*, the seats in use.

        Raises:
            RuntimeError: the OS memory or cpu cannot be read -- no supply is known, so nothing is admitted.

        """
        trees = {pid: process_tree(pid) for pid in pids}
        first, started = system_cpu_seconds(), time.monotonic()
        before = {pid: _tree_cpu(tree) for pid, tree in trees.items()}
        time.sleep(sample_s)
        second, wall = system_cpu_seconds(), max(time.monotonic() - started, 1e-6)
        memory = system_memory()
        if memory is None:
            msg = 'the box cannot read this machine memory, so it has no supply to admit against'
            raise RuntimeError(msg)
        cores = float(os.cpu_count() or 1)
        # no cpu backend (darwin): idle reads as every core, so the declared room alone decides cores
        idle = cores if first is None or second is None else min(cores, max(0.0, (second[1] - first[1]) / wall))
        usage = {
            pid: Usage(
                sum(working_set_bytes(member) or 0 for member in tree),
                max(0.0, (_tree_cpu(tree) - before[pid]) / wall),
            )
            for pid, tree in trees.items()
        }
        watch = watched(provided(section('capabilities')))
        seen, unseen = observe(watch, _process_rows()) if watch else ({}, {})
        return cls(cores, idle, memory.total_bytes, memory.available_bytes, usage, seen, unseen)


def observe(
    watch: Mapping[str, Sequence[str]], rows: Mapping[int, tuple[str, int]] | None
) -> tuple[dict[str, int], dict[str, str]]:
    """Each watched seat's count of running *rows* (``{pid: (image, parent)}``) whose image it names, case-blind.

    Returns ``(seen, unseen)``: with no table (*rows* ``None``) every seat is unseen, never zero in use.
    """
    if rows is None:
        return {}, dict.fromkeys(watch, 'the process table could not be read')
    images = [image.lower() for image, _ in rows.values()]
    wanted = {name: {image.lower() for image in names} for name, names in watch.items()}
    return {name: sum(image in names for image in images) for name, names in wanted.items()}, {}


def _tree_cpu(tree: Iterable[int]) -> float:
    return sum(times[1] for member in tree if (times := process_times(member)) is not None)


def room(
    capacity: Capacity,
    held: Sequence[Holding],
    reading: Reading,
    *,
    seats: Iterable[str] = (),
    asker: int | None = None,
) -> Resources:
    """What a NEW claim may take now: ``cpu``, ``mem_gb`` and each seat declared, held or named in *seats*.

    The module docstring's formula; a seat nobody declared counts 1, as a present undeclared tool does.
    *asker* is the pid asking: ITS OWN claims' unused part is its room (issue #5, 2026-10-19 -- a holder's
    nested ask is sequential with its own growth, since the holder waits on the child it hands the claim to),
    so for those only what is in use counts. A KEPT claim (taken for a run that adopts it) is not the asker's
    growth but that run's reservation, so it is owed like any other. Every other pid's unused part is still owed.
    """
    used = {h.pid: reading.usage.get(h.pid, Usage(0, 0.0)) for h in held}
    own = [h for h in held if h.pid == asker and not h.kept]
    others = [h for h in held if h.pid != asker or h.kept]
    cpu_owed = sum(max(0.0, h.cpu - used[h.pid].busy_cores) for h in others)
    mem_owed = sum(max(0, h.mem_bytes - used[h.pid].resident_bytes) for h in others)
    declared_cpu = sum(h.cpu for h in others) + sum(min(h.cpu, used[h.pid].busy_cores) for h in own)
    declared_mem = sum(h.mem_bytes for h in others) + sum(min(h.mem_bytes, used[h.pid].resident_bytes) for h in own)
    cpu = min(reading.idle_cores - capacity.kept_cpu - cpu_owed, capacity.cpu - declared_cpu)
    mem = min(reading.available_bytes - capacity.kept_bytes - mem_owed, capacity.mem_bytes - declared_mem)
    left = {
        name: _seats_left(name, capacity, held, reading)
        for name in {*capacity.seats, *seats, *(name for h in held for name in h.seats)}
    }
    return Resources({'cpu': max(0.0, cpu), 'mem_gb': max(0, mem) / GB, **{n: max(0, c) for n, c in left.items()}})


def _seats_left(name: str, capacity: Capacity, held: Sequence[Holding], reading: Reading) -> int:
    """The module docstring's seat formula for *name*; 0 when it is watched and was not observed."""
    claimed = sum(h.seats.get(name, 0) for h in held)
    if name in capacity.blind:
        return 0
    if name in capacity.watched:
        if name in reading.unseen or name not in reading.seats_in_use:
            return 0
        claimed = max(claimed, reading.seats_in_use[name])
    return capacity.seats.get(name, 1) - claimed - capacity.kept_seats.get(name, 0)
