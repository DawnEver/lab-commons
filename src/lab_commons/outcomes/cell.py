"""One task's outcome: a :class:`Cell` -- status, values, reason, evidence, cost.

The invariants are enforced at construction, so a cell that exists is a cell that is true:

- only ``OK`` and ``PARTIAL`` carry values -- a killed or rejected run has nothing to report;
- every status except ``OK`` and ``PENDING`` carries a :class:`Reason`;
- NaN is never a status: a value is a real number, never a failure marker.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from lab_commons.outcomes.status import Evidence, Status

__all__ = ['Cell', 'Cost', 'Reason']

#: A reason kind is a machine token: ``invalid``, ``infeasible``, ``assertion``, ``license``, ...
_TOKEN: Final = re.compile(r'[a-z][a-z0-9_]*')

#: The statuses that report values.
_CARRIES_VALUES: Final = frozenset({Status.OK, Status.PARTIAL})

#: The statuses that owe no reason: success, and not run yet.
_NEEDS_NO_REASON: Final = frozenset({Status.OK, Status.PENDING})


@dataclass(frozen=True)
class Reason:
    """Why a cell is not ``OK``: a machine *kind* and a human *text*."""

    kind: str
    text: str = ''

    def __post_init__(self) -> None:
        """Refuse a kind that is not a machine token."""
        if not isinstance(self.kind, str) or not _TOKEN.fullmatch(self.kind):
            msg = f'reason kind {self.kind!r} is not a machine token ([a-z][a-z0-9_]*)'
            raise ValueError(msg)


@dataclass(frozen=True)
class Cost:
    """What a task consumed. Each field is optional; unknown is ``None``, never zero."""

    wall_s: float | None = None
    cpu_s: float | None = None
    peak_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        """Refuse a negative or NaN measurement."""
        for name in ('wall_s', 'cpu_s', 'peak_memory_bytes'):
            value = getattr(self, name)
            if value is not None and not value >= 0:
                msg = f'cost {name}={value!r} is negative or NaN'
                raise ValueError(msg)


def _number(name: str, value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f'value {name!r}={value!r} is not a number'
        raise TypeError(msg)
    if isinstance(value, float) and math.isnan(value):
        msg = f'value {name!r} is NaN: NaN is never a status, give the cell a status and a reason'
        raise ValueError(msg)
    return value


@dataclass(frozen=True)
class Cell:
    """One task's outcome. See the module docstring for the invariants it enforces."""

    status: Status
    values: Mapping[str, float | int] = field(default_factory=dict)
    reason: Reason | None = None
    evidence: Evidence = Evidence.OBSERVED
    cost: Cost | None = None

    def __post_init__(self) -> None:
        """Enforce the invariants and freeze the values."""
        if not isinstance(self.status, Status):
            msg = f'status {self.status!r} is not a Status'
            raise TypeError(msg)
        values = {str(k): _number(str(k), v) for k, v in dict(self.values).items()}
        if values and self.status not in _CARRIES_VALUES:
            msg = f'a {self.status.name} cell carries no values (only OK and PARTIAL do): {sorted(values)}'
            raise ValueError(msg)
        if self.reason is None and self.status not in _NEEDS_NO_REASON:
            msg = f'a {self.status.name} cell owes a reason'
            raise ValueError(msg)
        object.__setattr__(self, 'values', MappingProxyType(values))
        object.__setattr__(self, 'evidence', Evidence(self.evidence))

    def __eq__(self, other: object) -> bool:
        """Equal on every part; values compare as plain mappings."""
        if not isinstance(other, Cell):
            return NotImplemented
        return (self.status, dict(self.values), self.reason, self.evidence, self.cost) == (
            other.status,
            dict(other.values),
            other.reason,
            other.evidence,
            other.cost,
        )

    __hash__ = None  # type: ignore[assignment]

    def to_plain(self) -> dict[str, Any]:
        """The JSON-ready form: statuses and evidence by token, absent parts omitted."""
        plain: dict[str, Any] = {'status': self.status.value, 'evidence': self.evidence.token}
        if self.values:
            plain['values'] = dict(self.values)
        if self.reason is not None:
            plain['reason'] = {'kind': self.reason.kind, 'text': self.reason.text}
        if self.cost is not None:
            plain['cost'] = {k: v for k, v in vars(self.cost).items() if v is not None}
        return plain

    @classmethod
    def from_plain(cls, plain: Mapping[str, Any]) -> Cell:
        """The inverse of :meth:`to_plain`; every invariant is re-checked."""
        reason = plain.get('reason')
        cost = plain.get('cost')
        return cls(
            Status(plain['status']),
            values=plain.get('values', {}),
            reason=None if reason is None else Reason(reason['kind'], reason.get('text', '')),
            evidence=Evidence.parse(plain.get('evidence', Evidence.OBSERVED.token)),
            cost=None if cost is None else Cost(**cost),
        )
