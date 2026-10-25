"""What a run asks for, and how large the box is.

A run DECLARES its demand -- a :class:`~lab_commons.compute.model.Demand` of cores, memory and named tool
seats -- and its wall-clock limit, and the limit is required: a default would be a number nobody
chose, and an undeclared demand is the exact defect this package exists to end (jobs claiming what
they do not use, or using what they never claimed). The declaration is also the run's enforced limit
(phase 2), so what is admitted is what the run can use and never more.

The :class:`Capacity` is the machine's SIZE: total cores and memory from the OS minus the machine file's
``[reserve]`` margins, read through :mod:`lab_commons.box.machine` -- the one home of
:func:`~lab_commons.box.machine.reserve_table`, :func:`~lab_commons.box.machine.own_reserve` and
:func:`~lab_commons.box.machine.provided` (``[capabilities.<name>] seats``), which the forge also reads.
A demand larger than the size can never be admitted. What may be admitted NOW is the measured room
(:func:`lab_commons.box.supply.room`), decided by the one rule, :func:`lab_commons.compute.admission.admit`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons.box.machine import own_reserve, provided, reserve_table, watched
from lab_commons.compute.durable import durable_text
from lab_commons.compute.model import Demand
from lab_commons.config import section
from lab_commons.outcomes import Status
from lab_commons.proc import system_memory

__all__ = [
    'CPU', 'GB', 'LENT_NAME', 'MEM_GB', 'NOT_ADMITTED', 'WALL_CEILING_S', 'Capacity', 'Claim', 'DuplicateKey',
    'Holding', 'NotAdmitted', 'ProcessEnd', 'Refused', 'checked', 'cores', 'declared_seats', 'mem_bytes',
    'not_admitted', 'read_ended', 'seat_supply', 'seats', 'write_ended',
]  # fmt: skip

#: Bytes in the gigabyte a demand's ``mem_gb`` is declared in (binary, as the OS reports totals).
GB = 2**30


class Refused(RuntimeError):
    """Admission said no: the message names the dimension short, its holders and their deadlines."""


#: The words every CAPACITY refusal carries, so its text still classifies after crossing a process boundary.
NOT_ADMITTED: Final = 'not admitted'


class NotAdmitted(Refused):
    """CAPACITY, not a verdict: the queue ran out while others held the room. Nothing ran; retry later."""


def not_admitted(text: str) -> bool:
    """Whether *text* (a refusal, or a log quoting one) is a capacity refusal -- the one classification."""
    return NOT_ADMITTED in text


class DuplicateKey(Refused):
    """An EXTERNAL claim under the same key is still unresolved: its job must not be submitted twice."""


#: The longest wall a claim may declare, and so the furthest future any ledger row may name. A row
#: past it (or one whose start lies in the future) is one this code never wrote: it is reaped on sight, so a
#: planted claim beside a live pid cannot pin a seat (a consumer SR-20261016-031).
WALL_CEILING_S: Final = 7 * 24 * 3600


#: The two names of a :class:`~lab_commons.compute.model.Demand` the box enforces itself; every other name is a seat.
CPU: Final = 'cpu'
MEM_GB: Final = 'mem_gb'


def checked(demand: Mapping[str, float], wall_s: int) -> Demand:
    """*demand* as the box books it, with its time limit *wall_s*: every bad dimension refused at once, by name.

    The run's size is the ONE budget type, :class:`~lab_commons.compute.model.Demand`; the box adds only that its
    cores and seats are whole and its wall is declared. Cores may be 0 for a claim that runs no compute of its
    own: a vendor GUI preparing a job holds memory and a seat but no core (D10, 2026-10-23). Memory may be 0 only
    for a claim that holds SEATS alone (an environment, a supervision target). A demand booking nothing is
    refused. A run :func:`~lab_commons.box.run` enforces declares both positive (it refuses otherwise).

    Raises:
        ValueError: naming every bad dimension.

    """
    asked = Demand(demand)
    wanted = seats(asked)
    bad = [
        *(['cpu must be a whole number of cores'] if not float(asked[CPU]).is_integer() else []),
        *(['mem_gb must be positive'] if asked[CPU] and not asked[MEM_GB] else []),
        *(['a demand must book something'] if not asked else []),
        *(['wall_s must be a positive int of seconds'] if not _positive_int(wall_s) else []),
        *([f'wall_s must not exceed WALL_CEILING_S ({WALL_CEILING_S})'] if _past_ceiling(wall_s) else []),
        *(f'seats[{name!r}] must be a whole count' for name, count in wanted.items() if count != asked[name]),
    ]
    if bad:
        msg = f'Demand: {"; ".join(bad)}'
        raise ValueError(msg)
    return asked


def cores(demand: Mapping[str, float]) -> int:
    """The whole cores *demand* books."""
    return int(demand.get(CPU, 0.0))


def mem_bytes(demand: Mapping[str, float]) -> int:
    """The memory *demand* books, in bytes: the unit the ledger and the kernel limit share."""
    return int(demand.get(MEM_GB, 0.0) * GB)


def seats(demand: Mapping[str, float]) -> dict[str, int]:
    """The seats *demand* books: every name but cores and memory, as whole counts."""
    return {name: int(amount) for name, amount in demand.items() if name not in {CPU, MEM_GB}}


def _past_ceiling(wall_s: object) -> bool:
    return isinstance(wall_s, int | float) and wall_s > WALL_CEILING_S


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def declared_seats() -> dict[str, int]:
    """The machine file's ``[capabilities]`` as ``{tool: seats}`` (default 1): what a box AND an account lend."""
    return {name: int(facts.get('seats', 1)) for name, facts in provided(section('capabilities')).items()}


#: Seconds a seat-supply probe may take before its tool falls back to the declared count.
SUPPLY_S = 10.0


def seat_supply() -> dict[str, int]:
    """Each ``[capabilities]`` tool's seats as MEASURED (D3): its ``supply`` probe's count, else its declaration.

    A ``supply`` argv prints the TOTAL seats this box may lend (a vendor's licence count, say) as one integer;
    the ledger subtracts what its claims hold. A probe that fails, times out or prints no count cannot say,
    and the declared ``seats`` stand in for it.
    """
    return {
        name: _measured(facts.get('supply')) or int(facts.get('seats', 1))
        for name, facts in provided(section('capabilities')).items()
    }


def _measured(supply: list[str] | None) -> int | None:
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


#: A LENT resource's name, ``<kind>:<instance>`` (``jobslot:ws1``): never a machine-file ``[capabilities]``
#: name.
LENT_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*:[A-Za-z0-9_.-]+$')


@dataclass(frozen=True)
class Capacity:
    """The machine's SIZE for admitted runs: cores and bytes after the reserve margins, and tool seats.

    ``kept_cpu`` and ``kept_bytes`` are those margins themselves, kept free above what the OS reads in use
    now (:func:`lab_commons.box.supply.room`); a size given by hand keeps none. ``kept_seats`` is the
    ``[reserve] seat_margin``, never lent; ``watched`` names the seats whose use is OBSERVED in the process
    table (``[capabilities.<name>] in_use``), so a reading that did not observe one lends none of it.
    """

    cpu: float
    mem_bytes: int
    seats: Mapping[str, int]
    kept_cpu: float = 0.0
    kept_bytes: int = 0
    kept_seats: Mapping[str, int] = field(default_factory=dict)
    watched: tuple[str, ...] = ()

    @classmethod
    def lent(cls, seats: Mapping[str, int]) -> Capacity:
        """A capacity of LENT resources alone: counts its consumer declares, no cores or memory.

        A lent name is ``<kind>:<instance>`` (:data:`LENT_NAME`), a shape the machine file's ``[capabilities]``
        names cannot take, so a lent count has ONE source -- its consumer's table -- and a machine-file
        declaration of the same name is impossible, not merely refused. The ledger knows nothing of what the
        resource is; every process claiming it must lend the same count (they read the same table).

        Raises:
            ValueError: a name is not ``<kind>:<instance>`` or a count is not a positive int.

        """
        bad = [
            f'{name!r}={count!r}'
            for name, count in seats.items()
            if not LENT_NAME.match(name) or not isinstance(count, int) or isinstance(count, bool) or count < 1
        ]
        if bad:
            msg = f'Capacity.lent: each name must match {LENT_NAME.pattern} and lend a positive int: {", ".join(bad)}'
            raise ValueError(msg)
        return cls(cpu=0.0, mem_bytes=0, seats=dict(seats))

    @classmethod
    def read(cls) -> Capacity:
        """The OS totals less the machine file's ``[reserve]``, plus its tools' MEASURED seats (:func:`seat_supply`)."""
        memory = system_memory()
        if memory is None:
            msg = 'the box cannot read this machine total memory, so it has no capacity to admit against'
            raise RuntimeError(msg)
        cores = float(os.cpu_count() or 1)
        total = {'cpu': cores, 'mem_gb': memory.total_bytes / GB}
        reserve = reserve_table(section('reserve'))
        kept = own_reserve(total, reserve)
        return cls(
            cpu=cores - kept['cpu'],
            mem_bytes=int(memory.total_bytes - kept['mem_gb'] * GB),
            seats=seat_supply(),
            kept_cpu=kept['cpu'],
            kept_bytes=int(kept['mem_gb'] * GB),
            kept_seats=dict(reserve.seat_margin),
            watched=tuple(sorted(watched(provided(section('capabilities'))))),
        )


@dataclass(frozen=True)
class Claim:
    """An admitted run's row: its id in the ledger and the moment its time limit ends."""

    id: int
    deadline: float


@dataclass(frozen=True)
class Holding:
    """One running claim as the ledger records it.

    ``id``, ``key`` and ``probe`` are its identity: any process may rebuild its :attr:`claim` and release it
    (an external job found finished offline). ``probe`` is ``None`` unless the claim is external.
    """

    what: str
    pid: int
    cpu: int
    mem_bytes: int
    seats: Mapping[str, int]
    deadline: float
    id: int = 0
    key: str = ''
    probe: tuple[str, ...] | None = None
    kept: bool = False

    @property
    def claim(self) -> Claim:
        """The :class:`Claim` this row is, for :func:`~lab_commons.box.ledger.release`."""
        return Claim(self.id, self.deadline)


@dataclass(frozen=True)
class ProcessEnd:
    """How an enforced run ended and what it measured -- the caller's to learn from.

    ``reason`` is ``exit`` (the child ended on its own), ``memory`` (the limit was hit and the tree
    killed) or ``wall`` (the time limit killed it). There is no ``cpu`` reason: a CPU cap throttles,
    it never kills. ``peak_mem_bytes`` is the tree's peak commit (Windows) or its largest process's
    peak RSS (Linux); ``cpu_s`` is user plus kernel time; ``enforcer`` names what held the limits.
    ``key`` and ``fingerprint`` are the caller's, returned unchanged (:func:`~lab_commons.box.run.run`).
    """

    returncode: int
    reason: str
    peak_mem_bytes: int
    cpu_s: float
    wall_s: float
    enforcer: str
    key: str
    fingerprint: str | None

    @property
    def killed(self) -> bool:
        """The box ended the tree (``memory`` or ``wall``); ``exit`` is the child's own ending."""
        return self.reason != 'exit'

    @property
    def status(self) -> Status | None:
        """The box's own verdict in the one vocabulary: a kill is RETRYABLE; ``exit`` carries none of its own."""
        return _KILLED_AS.get(self.reason)


#: How a box kill reads in :class:`~lab_commons.outcomes.Status` (plan A1.7).
_KILLED_AS: Final[Mapping[str, Status]] = {'memory': Status.OUT_OF_MEMORY, 'wall': Status.TIMEOUT}


def write_ended(path: Path, ended: ProcessEnd | Refused) -> None:
    """Record how a box run ended at *path*, atomically: ``{"ended": {...}}`` or ``{"refused": "<why>"}``.

    THE RECORD, NEVER THE EXIT CODE, carries the box's decision: the CLI exits with the child's own code,
    and a child may exit with any number, so no code can mean "the box refused" or "the box killed it".
    """
    record = {'refused': str(ended)} if isinstance(ended, Refused) else {'ended': asdict(ended)}
    durable_text(path, json.dumps(record))


def read_ended(path: Path) -> ProcessEnd | Refused | None:
    """How the box run recorded at *path* ended: an :class:`ProcessEnd`, a :class:`Refused`, or ``None``.

    A :class:`Refused` never ran. ``None`` is NO RECORD: the box itself died before it could write one
    (it crashed), which is neither a refusal nor anything the child did.
    """
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    return Refused(record['refused']) if 'refused' in record else ProcessEnd(**record['ended'])
