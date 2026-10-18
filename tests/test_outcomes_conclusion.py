"""A0.7: Conclusion -- PASS, FAIL or INCONCLUSIVE over a selection, starting INCONCLUSIVE."""

from __future__ import annotations

from lab_commons.outcomes import Cell, Conclusion, Expectation, Reason, Status

XFAIL = Expectation(Status.REJECTED, 'known residual')


def test_starts_inconclusive() -> None:
    assert Conclusion.over([]) is Conclusion.INCONCLUSIVE


def test_planted_control_an_expected_rejected_gives_pass() -> None:
    assert Conclusion.over([(Status.REJECTED, XFAIL), (Status.OK, None)]) is Conclusion.PASS


def test_planted_control_any_retryable_gives_inconclusive() -> None:
    for retryable in (s for s in Status if not s.decided):
        assert Conclusion.over([(Status.OK, None), (retryable, None)]) is Conclusion.INCONCLUSIVE, retryable


def test_any_decided_mismatch_gives_fail_even_beside_a_retryable() -> None:
    assert Conclusion.over([(Status.REJECTED, None)]) is Conclusion.FAIL
    assert Conclusion.over([(Status.OK, XFAIL)]) is Conclusion.FAIL, 'an unexpected pass is a mismatch'
    assert Conclusion.over([(Status.TIMEOUT, None), (Status.REJECTED, None)]) is Conclusion.FAIL


def test_cells_and_not_applicable() -> None:
    cells = [(Cell(Status.OK), None), (Cell(Status.NOT_APPLICABLE, reason=Reason('no_capability')), None)]
    assert Conclusion.over(cells) is Conclusion.PASS


def test_the_persisted_spellings_and_settledness_are_the_conclusions_own() -> None:
    """Plan A1.4: the dev verdict's three states ARE Conclusion; their stored tokens are unchanged."""
    assert [c.value for c in Conclusion] == ['inconclusive', 'pass', 'fail']
    assert [c.settled for c in Conclusion] == [False, True, True]
