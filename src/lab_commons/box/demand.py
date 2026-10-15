"""What a run asks for, what the box has, and the one pure rule that decides between them.

A run DECLARES its demand -- cores, memory, a wall-clock limit and named tool seats -- and every
field is required: a default would be a number nobody chose, and an undeclared demand is the
exact defect this package exists to end (jobs claiming what they do not use, or using what they
never claimed). The declaration is also the run's enforced limit (phase 2), so what is admitted
is what the run can use and never more.

The capacity is the MACHINE'S, not the moment's: total cores and memory from the OS minus the
machine file's ``[reserve]`` margins, read through :mod:`lab_commons.box.machine` -- the one home of
:func:`~lab_commons.box.machine.reserve_table`, :func:`~lab_commons.box.machine.own_reserve` and
:func:`~lab_commons.box.machine.provided` (``[capabilities.<name>] seats``), which the forge also reads.
"Available memory" right now is deliberately NOT consulted: admission sums declared limits, so
it cannot be fooled by a job that has not grown yet or blamed for one that never will.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lab_commons.box.machine import own_reserve, provided, reserve_table
from lab_commons.config import section
from lab_commons.proc import system_memory

__all__ = [
    'GB', 'Capacity', 'Demand', 'Holding', 'Outcome', 'Refused', 'declared_seats', 'fits', 'read_ended', 'write_ended',
]  # fmt: skip

#: Bytes in the gigabyte a :class:`Demand` is declared in (binary, as the OS reports totals).
GB = 2**30


class Refused(RuntimeError):
    """Admission said no: the message names the dimension short, its holders and their deadlines."""


@dataclass(frozen=True)
class Demand:
    """A run's declared resources and time limit. No field has a default; none may be non-positive."""

    cpu: int
    mem_gb: float
    wall_s: int
    seats: Mapping[str, int]

    def __post_init__(self) -> None:
        """Refuse every non-positive dimension at once, by name."""
        bad = [
            *(['cpu must be a positive int of cores'] if not _positive_int(self.cpu) else []),
            *(['mem_gb must be positive'] if not isinstance(self.mem_gb, int | float) or self.mem_gb <= 0 else []),
            *(['wall_s must be a positive int of seconds'] if not _positive_int(self.wall_s) else []),
            *(
                f'seats[{name!r}] must be an int >= 0'
                for name, count in self.seats.items()
                if not isinstance(count, int) or isinstance(count, bool) or count < 0
            ),
        ]
        if bad:
            msg = f'Demand: {"; ".join(bad)}'
            raise ValueError(msg)

    @property
    def mem_bytes(self) -> int:
        """The memory limit in bytes, the unit the ledger and the kernel limit share."""
        return int(self.mem_gb * GB)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def declared_seats() -> dict[str, int]:
    """The machine file's ``[capabilities]`` as ``{tool: seats}`` (default 1): what a box AND an account lend."""
    return {name: int(facts.get('seats', 1)) for name, facts in provided(section('capabilities')).items()}


@dataclass(frozen=True)
class Capacity:
    """What this machine lends to admitted runs: cores and bytes after the reserve, and tool seats."""

    cpu: float
    mem_bytes: int
    seats: Mapping[str, int]

    @classmethod
    def read(cls) -> Capacity:
        """The OS totals less the machine file's ``[reserve]``, plus its ``[capabilities]`` seats."""
        memory = system_memory()
        if memory is None:
            msg = 'the box cannot read this machine total memory, so it has no capacity to admit against'
            raise RuntimeError(msg)
        cores = float(os.cpu_count() or 1)
        total = {'cpu': cores, 'mem_gb': memory.total_bytes / GB}
        kept = own_reserve(total, {}, reserve_table(section('reserve')))
        return cls(
            cpu=cores - kept['cpu'],
            mem_bytes=int(memory.total_bytes - kept['mem_gb'] * GB),
            seats=declared_seats(),
        )


@dataclass(frozen=True)
class Holding:
    """One running claim as the ledger records it."""

    what: str
    pid: int
    cpu: int
    mem_bytes: int
    seats: Mapping[str, int]
    deadline: float


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec='seconds')


def fits(capacity: Capacity, running: Iterable[Holding], demand: Demand) -> str | None:
    """``None`` when *demand* fits beside *running* in every dimension, else why not.

    Pure: the sum of what is DECLARED plus *demand* against *capacity*, per dimension. The reason
    names the first short dimension and every holder of it with its deadline -- the moment the
    holder's claim ends at the latest, so a refused caller knows how long the wait can be.
    """
    held = list(running)
    rows: list[tuple[str, float, float, list[tuple[Holding, float]]]] = [
        ('cpu', capacity.cpu, demand.cpu, [(h, float(h.cpu)) for h in held]),
        ('memory', capacity.mem_bytes / GB, demand.mem_bytes / GB, [(h, h.mem_bytes / GB) for h in held]),
    ]
    rows += [
        (f'seat {name}', capacity.seats.get(name, 0), want, [(h, h.seats.get(name, 0)) for h in held])
        for name, want in sorted(demand.seats.items())
        if want
    ]
    for dimension, total, want, uses in rows:
        holders = [(h, amount) for h, amount in uses if amount]
        if sum(amount for _, amount in holders) + want <= total:
            continue
        named = ', '.join(f'{h.what} (pid {h.pid}) holds {amount:g} until {_when(h.deadline)}' for h, amount in holders)
        return f'{dimension}: wants {want:g} of {total:g}; ' + (named or 'no holder -- it can never fit')
    return None


@dataclass(frozen=True)
class Outcome:
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


def write_ended(path: Path, ended: Outcome | Refused) -> None:
    """Record how a box run ended at *path*, atomically: ``{"outcome": {...}}`` or ``{"refused": "<why>"}``.

    THE RECORD, NEVER THE EXIT CODE, carries the box's decision: the CLI exits with the child's own code,
    and a child may exit with any number, so no code can mean "the box refused" or "the box killed it".
    """
    record = {'refused': str(ended)} if isinstance(ended, Refused) else {'outcome': asdict(ended)}
    staged = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    staged.write_text(json.dumps(record), encoding='utf-8')
    staged.replace(path)


def read_ended(path: Path) -> Outcome | Refused | None:
    """How the box run recorded at *path* ended: an :class:`Outcome`, a :class:`Refused`, or ``None``.

    A :class:`Refused` never ran. ``None`` is NO RECORD: the box itself died before it could write one
    (it crashed), which is neither a refusal nor anything the child did.
    """
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    return Refused(record['refused']) if 'refused' in record else Outcome(**record['outcome'])
