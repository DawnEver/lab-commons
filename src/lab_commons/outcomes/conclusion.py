"""What a selection of tasks concludes: PASS, FAIL or INCONCLUSIVE.

PASS only when every cell is DECIDED and meets its expectation; FAIL when any DECIDED cell does not
(a wrong answer is wrong whatever else is missing); otherwise INCONCLUSIVE -- including the empty
selection, because a conclusion starts INCONCLUSIVE and only evidence moves it.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable

from lab_commons.outcomes.cell import Cell
from lab_commons.outcomes.expectation import Expectation
from lab_commons.outcomes.status import Status

__all__ = ['Conclusion']


class Conclusion(enum.Enum):
    """The aggregate over a selection of task outcomes."""

    #: The values are the persisted spellings (verdict logs, run caches, forge statuses).
    INCONCLUSIVE = 'inconclusive'
    PASS = 'pass'  # noqa: S105 -- a conclusion, not a credential
    FAIL = 'fail'

    @property
    def settled(self) -> bool:
        """Whether the selection PROVED an answer, as opposed to being unable to say."""
        return self is not Conclusion.INCONCLUSIVE

    @classmethod
    def over(cls, selection: Iterable[tuple[Cell | Status, Expectation | None]]) -> Conclusion:
        """Conclude over ``(cell or status, expectation)`` pairs; a ``None`` expectation means ``OK``."""
        answered = unanswered = False
        for outcome, expectation in selection:
            status = outcome.status if isinstance(outcome, Cell) else outcome
            met = (expectation or Expectation.default()).met_by(status)
            if met is False:
                return cls.FAIL
            answered = True
            unanswered = unanswered or met is None
        return cls.PASS if answered and not unanswered else cls.INCONCLUSIVE
