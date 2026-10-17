"""What a task is DECLARED to conclude (``OK``, or a known ``REJECTED``), and why.

An expectation is part of the task declaration and is compared against the status; it is never a
status itself (an xfail is ``REJECTED`` under ``Expectation(REJECTED)``, not a thirteenth status).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.outcomes.status import Status

__all__ = ['Expectation']

#: The statuses a task can be declared to reach.
_EXPECTABLE: Final = frozenset({Status.OK, Status.REJECTED})


@dataclass(frozen=True)
class Expectation:
    """The declared conclusion of a task and the reason it is declared."""

    status: Status
    why: str

    def __post_init__(self) -> None:
        """Refuse an expectation of a non-conclusion, or one that does not say why."""
        if self.status not in _EXPECTABLE:
            msg = f'only OK or REJECTED can be expected, not {self.status!r}'
            raise ValueError(msg)
        if not self.why.strip():
            msg = 'an expectation must say why'
            raise ValueError(msg)

    @classmethod
    def default(cls) -> Expectation:
        """The expectation of a task that declares none: ``OK``."""
        return cls(Status.OK, 'the declared default')

    def met_by(self, status: Status) -> bool | None:
        """True or False for a DECIDED *status*; ``None`` for a RETRYABLE one, which answers nothing.

        ``NOT_APPLICABLE`` meets every expectation: the question does not apply, so nothing was missed.
        """
        if not status.decided:
            return None
        return status is Status.NOT_APPLICABLE or status is self.status

    def to_plain(self) -> dict[str, Any]:
        """The JSON-ready form."""
        return {'status': self.status.value, 'why': self.why}

    @classmethod
    def from_plain(cls, plain: Mapping[str, Any]) -> Expectation:
        """The inverse of :meth:`to_plain`."""
        return cls(Status(plain['status']), plain['why'])
