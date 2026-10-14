"""What a run asks for, what the box has, and the one pure rule that decides between them.

A run DECLARES its demand -- cores, memory, a wall-clock limit and named tool seats -- and every
field is required: a default would be a number nobody chose, and an undeclared demand is the
exact defect this package exists to end (jobs claiming what they do not use, or using what they
never claimed). The declaration is also the run's enforced limit (phase 2), so what is admitted
is what the run can use and never more.

The capacity is the MACHINE'S, not the moment's: total cores and memory from the OS minus the
machine file's ``[reserve]`` margins, read through the SAME :func:`lab_commons.compute.ledger
.reserve_table` / :func:`~lab_commons.compute.ledger.own_reserve` compute uses, plus the
``[capabilities.<name>] seats`` read through :func:`lab_commons.dev.forgeprovides.provided`.
"Available memory" right now is deliberately NOT consulted: admission sums declared limits, so
it cannot be fooled by a job that has not grown yet or blamed for one that never will.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from lab_commons.compute.ledger import own_reserve, reserve_table
from lab_commons.config import section
from lab_commons.dev.forgeprovides import provided
from lab_commons.proc import system_memory

__all__ = ['GB', 'Capacity', 'Demand', 'Holding', 'Refused', 'fits']

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
        seats = {name: int(facts.get('seats', 1)) for name, facts in provided(section('capabilities')).items()}
        return cls(
            cpu=cores - kept['cpu'],
            mem_bytes=int(memory.total_bytes - kept['mem_gb'] * GB),
            seats=seats,
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
