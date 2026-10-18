"""What happened to a task, whose fault it is, and how far to trust it.

A :class:`Status` belongs to exactly one :class:`Family`. DECIDED is a conclusion about the SUBJECT
(it passed, it was rejected, the question does not apply to it); RETRYABLE is no conclusion at all
(not run yet, killed, timed out, the infrastructure failed). The defect this split exists to refuse is
a memory kill recorded as ``failed``: that blames the subject for the machine.

:class:`Evidence` orders how a value was obtained, weakest first.
"""

from __future__ import annotations

import enum
from typing import Final

__all__ = ['Evidence', 'Family', 'Status']


class Family(enum.Enum):
    """Whether a status is a conclusion about the subject."""

    DECIDED = 'decided'
    RETRYABLE = 'retryable'


class Status(enum.Enum):
    """The twelve task statuses; each knows its :attr:`family`."""

    OK = 'ok'
    REJECTED = 'rejected'
    NOT_APPLICABLE = 'not_applicable'
    PENDING = 'pending'
    SCREENED = 'screened'
    PARTIAL = 'partial'
    TIMEOUT = 'timeout'
    OUT_OF_MEMORY = 'out_of_memory'
    UNAVAILABLE = 'unavailable'
    NONCONVERGED = 'nonconverged'
    INFRA = 'infra'
    ERROR = 'error'

    @property
    def family(self) -> Family:
        """DECIDED for a conclusion about the subject, else RETRYABLE."""
        return Family.DECIDED if self in _DECIDED else Family.RETRYABLE

    @property
    def decided(self) -> bool:
        """True when this status is a conclusion about the subject."""
        return self.family is Family.DECIDED


#: The one place the partition is stated; every other family is RETRYABLE.
_DECIDED: Final = frozenset({Status.OK, Status.REJECTED, Status.NOT_APPLICABLE})


class Evidence(enum.IntEnum):
    """How a value was obtained: ``PREDICTED < OBSERVED < VALIDATED < CERTIFIED``."""

    PREDICTED = 1
    OBSERVED = 2
    VALIDATED = 3
    CERTIFIED = 4

    @property
    def token(self) -> str:
        """The persisted spelling: the lower-case name, never the ordinal."""
        return self.name.lower()

    @classmethod
    def parse(cls, token: str) -> Evidence:
        """The member whose :attr:`token` is *token*; an unknown token raises ``ValueError``."""
        try:
            return cls[token.upper()]
        except KeyError:
            msg = f'unknown evidence {token!r}'
            raise ValueError(msg) from None
