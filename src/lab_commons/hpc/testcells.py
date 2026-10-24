"""ONE TEST'S RESULT -- a :class:`~lab_commons.outcomes.Cell` under its declared Expectation.

Every reader of a test run reads this shape: a local run's per-worker :class:`~lab_commons.outcomes.Record`
(:mod:`dev_lab.dev.test_outcomes`), a cluster item's stream line (:mod:`lab_commons.hpc.pytest_item`),
a run's state and its run record (:mod:`lab_commons.hpc.records`). The plain form is the cell's own
(:meth:`~lab_commons.outcomes.Cell.to_plain`) plus ``expectation`` when the test declares one (an xfail
declares ``REJECTED``), so a stream line IS a plain test cell with its id beside it.

A run CONCLUDES by :meth:`~lab_commons.outcomes.Conclusion.over` its cells -- PASS when every one is
DECIDED and meets its expectation, FAIL on any decided miss, else INCONCLUSIVE -- and a FLAKY id (the same
id DECIDED both ways within one run) makes a would-be PASS INCONCLUSIVE: a single run's conclusion is
decided, so two opposite decisions mean the run proved nothing about that id. It is never overwritten.

An id a run HANDS to another part is no cell of this run: the record's ``handed`` says so, nothing else.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.hpc.retired import TEST_WORDS
from lab_commons.outcomes import Cell, Conclusion, Expectation, Reason, Status

__all__ = ['FLAKY', 'TestCell', 'conclude', 'counts', 'merged', 'upgraded']

#: The caller event kind a Record carries when one id is DECIDED both ways in one run.
FLAKY: Final = 'flaky'

#: How :meth:`TestCell.word` marks a cell declared to be REJECTED (an xfail).
_EXPECTED_REJECTED: Final = '/expected-rejected'


@dataclass(frozen=True)
class TestCell:
    """What one test concluded (*cell*) and what it is declared to conclude (*expectation*; ``None`` is ``OK``)."""

    __test__ = False  # a value type, not a pytest class

    cell: Cell
    expectation: Expectation | None = None

    @property
    def status(self) -> Status:
        """The cell's status."""
        return self.cell.status

    @property
    def met(self) -> bool | None:
        """Whether a DECIDED cell meets the expectation; ``None`` for a RETRYABLE one, which answers nothing."""
        return (self.expectation or Expectation.default()).met_by(self.cell.status)

    @property
    def red(self) -> bool:
        """A DECIDED miss: the test answered, and not as declared."""
        return self.met is False

    @property
    def why(self) -> str:
        """The reason's text, or ``''``."""
        reason = self.cell.reason
        return '' if reason is None else reason.text

    def word(self) -> str:
        """The status token, ``/expected-rejected`` appended under an expectation of ``REJECTED``."""
        expected = self.expectation is not None and self.expectation.status is Status.REJECTED
        return self.cell.status.value + (_EXPECTED_REJECTED if expected else '')

    def to_plain(self) -> dict[str, Any]:
        """The cell's plain form, plus ``expectation`` when one is declared."""
        plain = self.cell.to_plain()
        if self.expectation is not None:
            plain['expectation'] = self.expectation.to_plain()
        return plain

    @classmethod
    def from_plain(cls, plain: Mapping[str, Any]) -> TestCell:
        """The inverse of :meth:`to_plain`; keys beside it (a stream line's ``id``, ``s``, ``item``) are ignored."""
        expectation = plain.get('expectation')
        return cls(Cell.from_plain(plain), None if expectation is None else Expectation.from_plain(expectation))


def conclude(cells: Iterable[TestCell], flaky: Collection[str] = ()) -> Conclusion:
    """The conclusion over *cells*: :meth:`Conclusion.over`, a PASS made INCONCLUSIVE by any *flaky* id."""
    over = Conclusion.over((one.cell, one.expectation) for one in cells)
    return Conclusion.INCONCLUSIVE if flaky and over is Conclusion.PASS else over


def counts(cells: Iterable[TestCell]) -> dict[str, int]:
    """How many cells read each :meth:`TestCell.word`."""
    found: dict[str, int] = {}
    for one in cells:
        found[one.word()] = found.get(one.word(), 0) + 1
    return found


def merged(sources: Iterable[Mapping[str, TestCell]]) -> tuple[dict[str, TestCell], frozenset[str]]:
    """``(cells, flaky ids)`` over several processes' cells of ONE run.

    A DECIDED cell is final: a later RETRYABLE one for its id does not replace it, a later RETRYABLE one
    replaces a RETRYABLE one, and two DECIDED cells that differ make the id flaky (the first is kept).
    """
    cells: dict[str, TestCell] = {}
    flaky: set[str] = set()
    for source in sources:
        for node, one in source.items():
            held = cells.get(node)
            if held is None or not held.status.decided:
                cells[node] = one
            elif one.status.decided and one.cell != held.cell:
                flaky.add(node)
    return cells, frozenset(flaky)


def upgraded(word: str, why: str = '') -> TestCell | None:
    """The cell a retired per-test *word* of LIVE state reads as (:data:`lab_commons.hpc.retired.TEST_WORDS`).

    ``None`` for a handed id, which is no cell; an unknown word raises ``ValueError``.
    """
    if word not in TEST_WORDS:
        msg = f'{word!r} is no retired per-test word ({sorted(TEST_WORDS)})'
        raise ValueError(msg)
    reads = TEST_WORDS[word]
    if reads is None:
        return None
    spelled, kind = reads
    token, expected, _ = spelled.partition(_EXPECTED_REJECTED)
    expectation = Expectation(Status.REJECTED, why or 'xfail') if expected else None
    return TestCell(Cell(Status(token), reason=Reason(kind, why) if kind else None), expectation)
