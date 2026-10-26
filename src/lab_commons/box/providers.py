"""THE BOX'S PROVIDERS (:class:`~lab_commons.resources.Provider`): its OS, its tool seats, and lent counts.

Each MEASURES this box less what is used OUTSIDE the ledger; the engine (:mod:`lab_commons.resources.engine`)
subtracts what the ledger holds. For cores and memory that is the measured room (:func:`lab_commons.box.supply.room`)
plus every claim's declaration: the OS free now and what the claims themselves use, within the box's size. For a
seat it is its size (:func:`seat_sizes`) less what is observed in use beyond the claims' holdings. Nothing is held
back for people or anyone else (user ruling 2026-10-26): a person's use is a declared claim (``box claim``).

THE ONE SEAT READER is here: :func:`seat_sizes` (each ``[capabilities]`` tool's supply probe, else its declared
count) sizes the box's :class:`~lab_commons.box.Capacity`, and :class:`ToolSeats` counts what of it is free.
Whether a tool is INSTALLED is a separate fact (:mod:`lab_commons.compute.presence`): given a presence reading, a
checked tool it does not find lends nothing and is absent; the box ledger, which has no needs to check, gives none.

:func:`measured` is the same arithmetic on a reading already taken: the ledger's decision and its queue
(:mod:`lab_commons.box.ledger`, :mod:`lab_commons.box.order`) read the OS before the write lock and decide under it.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, closing, contextmanager
from dataclasses import dataclass

import lab_commons.launch as _door
from lab_commons.box._store import connect
from lab_commons.box._store import read as read_ledger
from lab_commons.box.demand import GB, Capacity, Holding
from lab_commons.box.machine import provided
from lab_commons.box.supply import Reading, room
from lab_commons.compute.model import Resources, Supply
from lab_commons.compute.presence import Evidence, Presence
from lab_commons.compute.probe import os_name
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.config import section
from lab_commons.resources.engine import Engine
from lab_commons.resources.model import Actual, Claim

__all__ = [
    'SUPPLY_S', 'BoxOS', 'Lent', 'Read', 'ToolSeats', 'account_seats', 'box_engine', 'inventory',
    'measured', 'seat_sizes', 'size',
]  # fmt: skip

#: The OS reader a measure samples with: the claim pids in, a :class:`~lab_commons.box.supply.Reading` out.
Read = Callable[[Iterable[int]], Reading]

#: Seconds a seat-supply probe may take before its tool is blind.
SUPPLY_S = 10.0


def seat_sizes() -> tuple[dict[str, int], dict[str, str]]:
    """Each ``[capabilities]`` tool's seats as MEASURED: ``(sizes, blind)``.

    A ``supply`` argv prints the TOTAL seats this box may lend (a vendor's licence count, say) as one integer;
    the ledger subtracts what its claims hold. No probe: the declared ``seats`` are the size. A probe that fails,
    times out or prints no count CANNOT SAY: its tool keeps its declared size but is *blind* -- ``{tool: why}``
    -- and lends none of it (a vacuous green is the family's worst defect).
    """
    sizes: dict[str, int] = {}
    blind: dict[str, str] = {}
    for name, facts in provided(section('capabilities')).items():
        probe = facts.get('supply')
        count = _counted(probe)
        if probe and count is None:
            blind[name] = f'its supply probe {probe[0]!r} failed, timed out or printed no count'
        sizes[name] = count if count is not None else int(facts.get('seats', 1))
    return sizes, blind


def _counted(supply: list[str] | None) -> int | None:
    """The count *supply* prints, or ``None`` when there is no probe or it cannot say."""
    if not supply:
        return None
    try:
        done = _door.run(
            supply, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=SUPPLY_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = done.stdout.strip()
    return int(text) if done.returncode == 0 and text.isdecimal() else None


def inventory(declared: Iterable[str], presence: Mapping[str, Presence]) -> dict[str, Presence]:
    """Every tool of this host with its verdict: checked ones as *presence* read them, other *declared* ones present."""
    unchecked = {
        name: Presence(name, present=True, evidence=(Evidence('declared, no check', '[capabilities]', ''),))
        for name in declared
        if name not in CHECKS
    }
    return {**presence, **unchecked}


def _seats(
    capacity: Capacity,
    held: Sequence[Holding],
    reading: Reading,
    names: Collection[str],
    presence: Mapping[str, Presence] | None,
) -> tuple[dict[str, float], frozenset[str]]:
    """Each seat of *names* as supplied: free (:func:`~lab_commons.box.supply.room`) plus what claims hold of it.

    With *presence*, a checked tool it does not find lends nothing and is absent.
    """
    if presence is not None:
        found = inventory(capacity.seats, presence)
        names = [name for name in names if name not in CHECKS or (name in found and found[name].present)]
    free = room(capacity, held, reading, seats=names)
    claimed = {name: sum(h.seats.get(name, 0) for h in held) for name in names}
    return {name: free[name] + claimed[name] for name in names}, frozenset(names)


def _os(
    capacity: Capacity, held: Sequence[Holding], reading: Reading, *, asker: int | None, platform: str
) -> Resources:
    """``cpu``, ``mem_gb`` and the OS as supplied: the room plus what the claims declare; the OS counts as ``cpu``."""
    free = room(capacity, held, reading, asker=asker)
    cpu = free['cpu'] + sum(h.cpu for h in held)
    mem_gb = free['mem_gb'] + sum(h.mem_bytes for h in held) / GB
    return Resources({'cpu': cpu, 'mem_gb': mem_gb, os_name(platform): cpu})


def measured(
    capacity: Capacity,
    held: Sequence[Holding],
    reading: Reading,
    *,
    seats: Collection[str] = (),
    asker: int | None = None,
    presence: Mapping[str, Presence] | None = None,
    platform: str = sys.platform,
) -> Supply:
    """The box's supply on a *reading* already taken: :class:`BoxOS` and :class:`ToolSeats` over *seats*.

    *asker* is the pid asking: its own claims' unused part is its room (:func:`~lab_commons.box.supply.room`).
    """
    counts, present = _seats(capacity, held, reading, {*capacity.seats, *seats}, presence)
    system = _os(capacity, held, reading, asker=asker, platform=platform)
    return Supply('box', system + Resources(counts), present | system.names | {'cpu', 'mem_gb'})


def size(capacity: Capacity, seats: Collection[str] = ()) -> Supply:
    """The box's whole SIZE for admitted runs, nothing running: a demand it does not admit never fits here."""
    whole = Resources(
        {'cpu': capacity.cpu, 'mem_gb': capacity.mem_bytes / GB, **{n: capacity.seats.get(n, 1) for n in seats}}
    )
    return Supply('box', whole, whole.names | {'cpu'})


def _ledger(read: Read) -> tuple[list[Holding], Reading]:
    """The box ledger's claims now and *read* of their trees."""
    with closing(connect()) as con:
        held = read_ledger(con)
    return held, read([h.pid for h in held])


@contextmanager
def _unenforced() -> Iterator[Actual]:
    """A box claim's limits are applied by the run that holds it (:func:`lab_commons.box.run`), not by its provider."""
    yield Actual()


@dataclass(frozen=True)
class BoxOS:
    """The box's cores, memory and OS (``windows``/``linux``/``macos``, counted as ``cpu``).

    *capacity* is its size (default: :meth:`~lab_commons.box.Capacity.read`), *read* the OS reader.
    """

    capacity: Capacity | None = None
    read: Read = Reading.now
    platform: str = sys.platform

    @property
    def names(self) -> frozenset[str]:
        """``cpu``, ``mem_gb`` and this OS."""
        return frozenset({'cpu', 'mem_gb', os_name(self.platform)})

    def measure(self, scope: str) -> Supply:
        """Room plus every claim's declaration, read now: what lies outside the ledger, for any asker."""
        capacity = Capacity.read() if self.capacity is None else self.capacity
        held, reading = _ledger(self.read)
        system = _os(capacity, held, reading, asker=None, platform=self.platform)
        return Supply(scope, system, system.names | {'cpu', 'mem_gb'})

    def enforce(self, scope: str, claim: Claim) -> AbstractContextManager[Actual]:
        """See :func:`_unenforced`."""
        del scope, claim
        return _unenforced()


@dataclass(frozen=True)
class ToolSeats:
    """Each tool seat of a scope: its size less what is observed in use beyond the claims' holdings.

    *capacity* carries the sizes (:func:`seat_sizes`), the blind probes and the observed seats; *read* observes
    them; *presence*, when given, keeps only the tools it finds (a checked tool it misses lends nothing). Its names
    are every sized or checked tool.
    """

    capacity: Capacity
    read: Read = Reading.now
    presence: Mapping[str, Presence] | None = None

    @property
    def names(self) -> frozenset[str]:
        """Every tool this scope may count."""
        return frozenset(self.capacity.seats) | frozenset(CHECKS)

    def measure(self, scope: str) -> Supply:
        """Each present tool's size less what is observed in use beyond the claims (a blind one lends none)."""
        held, reading = _ledger(self.read)
        counts, present = _seats(self.capacity, held, reading, self.names, self.presence)
        return Supply(scope, Resources(counts), present)

    def enforce(self, scope: str, claim: Claim) -> AbstractContextManager[Actual]:
        """See :func:`_unenforced`."""
        del scope, claim
        return _unenforced()


@dataclass(frozen=True)
class Lent:
    """Counts a consumer LENDS the box: ``<kind>:<instance>`` (:meth:`~lab_commons.box.Capacity.lent`).

    A JMAG Job System's slots on a node, say. Its consumer's table is the one source; every process lends the same.
    """

    counts: Mapping[str, int]

    @property
    def names(self) -> frozenset[str]:
        """The lent names."""
        return frozenset(Capacity.lent(self.counts).seats)

    def measure(self, scope: str) -> Supply:
        """The lent counts: nothing outside the ledger uses a lent name."""
        lent = Resources(Capacity.lent(self.counts).seats)
        return Supply(scope, lent, lent.names)

    def enforce(self, scope: str, claim: Claim) -> AbstractContextManager[Actual]:
        """See :func:`_unenforced`."""
        del scope, claim
        return _unenforced()


def _nothing_observed(pids: Iterable[int]) -> Reading:
    """No OS reading: an account's seats are not observed in a process table."""
    del pids
    return Reading(cores=0.0, idle_cores=0.0, total_bytes=0, available_bytes=0)


def account_seats(presence: Mapping[str, Presence] | None) -> ToolSeats:
    """The one seat provider on a cluster ACCOUNT's scope: its sizes measured where this runs, nothing observed."""
    sizes, blind = seat_sizes()
    return ToolSeats(Capacity(cpu=0.0, mem_bytes=0, seats=sizes, blind=blind), _nothing_observed, presence)


def box_engine(
    *,
    capacity: Capacity | None = None,
    read: Read = Reading.now,
    presence: Mapping[str, Presence] | None = None,
    lent: Mapping[str, int] | None = None,
    platform: str = sys.platform,
) -> Engine:
    """The engine over this box's scope ``'box'``: :class:`BoxOS`, :class:`ToolSeats` and, given, :class:`Lent`."""
    capacity = Capacity.read() if capacity is None else capacity
    plugs: list[BoxOS | ToolSeats | Lent] = [BoxOS(capacity, read, platform), ToolSeats(capacity, read, presence)]
    return Engine({'box': [*plugs, *([Lent(lent)] if lent else [])]})
