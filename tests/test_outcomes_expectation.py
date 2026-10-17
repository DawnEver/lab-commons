"""A0.4: an Expectation is part of the task declaration, compared against a status, never a status."""

from __future__ import annotations

import pytest

from lab_commons.outcomes import Expectation, Status


def test_only_ok_or_rejected_can_be_expected() -> None:
    assert Expectation(Status.REJECTED, 'known residual 4 %').status is Status.REJECTED
    for status in set(Status) - {Status.OK, Status.REJECTED}:
        with pytest.raises(ValueError, match='expect'):
            Expectation(status, 'why')


def test_an_expectation_says_why() -> None:
    with pytest.raises(ValueError, match='why'):
        Expectation(Status.REJECTED, '')
    assert Expectation.default() == Expectation(Status.OK, 'the declared default')


def test_met_by_a_decided_status_only() -> None:
    """PLANTED CONTROL: a RETRYABLE status neither meets nor breaks an expectation -- it is no answer."""
    xfail = Expectation(Status.REJECTED, 'known')
    assert xfail.met_by(Status.REJECTED) is True
    assert xfail.met_by(Status.OK) is False
    assert xfail.met_by(Status.TIMEOUT) is None
    assert Expectation.default().met_by(Status.NOT_APPLICABLE) is True, 'not applicable answers no question'


def test_round_trips_through_its_plain_form() -> None:
    xfail = Expectation(Status.REJECTED, 'known residual')
    assert Expectation.from_plain(xfail.to_plain()) == xfail
