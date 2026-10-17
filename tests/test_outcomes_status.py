"""A0.1: the two families, the twelve statuses each knowing its family, and the evidence order."""

from __future__ import annotations

from lab_commons.outcomes import Evidence, Family, Status

#: The ratified partition (PLAN-engines-and-one-outcome-vocabulary II.3), as NAMED SETS.
DECIDED = {'OK', 'REJECTED', 'NOT_APPLICABLE'}
RETRYABLE = {
    'PENDING',
    'SCREENED',
    'PARTIAL',
    'TIMEOUT',
    'OUT_OF_MEMORY',
    'UNAVAILABLE',
    'NONCONVERGED',
    'INFRA',
    'ERROR',
}


def test_two_families() -> None:
    assert {f.name for f in Family} == {'DECIDED', 'RETRYABLE'}


def test_every_status_knows_its_family() -> None:
    assert {s.name for s in Status if s.family is Family.DECIDED} == DECIDED
    assert {s.name for s in Status if s.family is Family.RETRYABLE} == RETRYABLE
    assert {s.name for s in Status} == DECIDED | RETRYABLE


def test_planted_control_an_oom_kill_is_not_a_decision() -> None:
    """PLANTED CONTROL: the B0-06 defect is a memory kill recorded as a conclusion about the subject."""
    assert Status.OUT_OF_MEMORY.family is Family.RETRYABLE
    assert Status.REJECTED.family is Family.DECIDED


def test_evidence_is_ordered() -> None:
    assert Evidence.PREDICTED < Evidence.OBSERVED < Evidence.VALIDATED < Evidence.CERTIFIED
    assert [e.name for e in sorted(Evidence)] == ['PREDICTED', 'OBSERVED', 'VALIDATED', 'CERTIFIED']


def test_tokens_round_trip() -> None:
    for status in Status:
        assert Status(status.value) is status
    for evidence in Evidence:
        assert Evidence.parse(evidence.token) is evidence
