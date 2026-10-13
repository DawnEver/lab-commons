"""THE RULE'S LENDING AND PREEMPTION, as tables (user ruling 2026-10-13).

A reservation lends what its holders leave idle; a holder's job preempts loans in its lease, newest first and
only as many as needed. Each row with a refusal or a preemption has its PLANTED CONTROL beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lab_commons.compute.admission import Admission, Held, admit, claimable, idle, preempt
from lab_commons.compute.model import For, Job, Reservation, Resources, Supply

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
BOX = Supply('G', Resources(cpu=16))
LEASE = Reservation('G', Resources(cpu=8), For(session='lane/x'), LATER, 'alice', id=7)
HOLDER = Job('o/r', 'a' * 40, 'pytest', session='lane/x')
FOREIGN = Job('o/r', 'b' * 40, 'pytest', session='lane/y')


def _loan(cpu: float, minutes: int, sha: str = 'c') -> Held:
    return Held(
        Resources(cpu=cpu), Job('o/r', sha * 40, 'pytest'), on_loan=LEASE.id, since=NOW - timedelta(minutes=minutes)
    )


def _admit(job: Job, cpu: float, running: tuple[Held, ...] = (), lease: Reservation = LEASE) -> Admission:
    return admit(job, Resources(cpu=cpu), BOX, reservations=(lease,), running=running, now=NOW)


@pytest.mark.parametrize(
    ('job', 'cpu', 'running', 'expected'),
    [
        (FOREIGN, 8, (), Admission(admitted=True)),  # the unreserved half is free
        (FOREIGN, 12, (), Admission(admitted=True, on_loan=7)),  # beyond it: borrowed from the lease
        (FOREIGN, 17, (), Admission(admitted=False, lacking=('cpu',))),  # CONTROL: past the box
        (HOLDER, 12, (), Admission(admitted=True)),  # a holder sees its own lease as free
        # The holder's own work running counts against its lease, so less of it is idle to lend.
        (FOREIGN, 9, (Held(Resources(cpu=6), HOLDER),), Admission(admitted=True, on_loan=7)),
        (FOREIGN, 8, (Held(Resources(cpu=6), HOLDER),), Admission(admitted=True)),  # CONTROL: 16 - 6 - 2 idle
    ],
)
def test_who_fits_and_who_borrows(job: Job, cpu: float, running: tuple[Held, ...], expected: Admission) -> None:
    assert _admit(job, cpu, running) == expected


def test_a_holder_blocked_by_loans_preempts_the_newest_and_only_as_many_as_needed() -> None:
    old, new = _loan(6, minutes=60, sha='o'), _loan(6, minutes=5, sha='n')
    running = (old, new)  # 12 of 16 used, all on loan from the lease
    assert _admit(HOLDER, 6, running) == Admission(admitted=True, preempt=(new,))
    assert _admit(HOLDER, 12, running) == Admission(admitted=True, preempt=(new, old))


def test_a_holder_with_room_elsewhere_preempts_nothing() -> None:
    """PLANTED CONTROL for the test above: the same loans, a holder job that fits beside them."""
    running = (_loan(6, minutes=60, sha='o'), _loan(6, minutes=5, sha='n'))
    assert _admit(HOLDER, 4, running) == Admission(admitted=True)


def test_a_foreign_job_never_preempts() -> None:
    running = (_loan(6, minutes=60, sha='o'), _loan(6, minutes=5, sha='n'))
    assert _admit(FOREIGN, 6, running) == Admission(admitted=False, lacking=('cpu',))


def test_a_whole_scope_lease_lends_everything_its_holders_leave() -> None:
    whole = Reservation('G', Resources(), For(session='lane/x'), LATER, 'alice', id=9, whole=True)
    assert idle(whole, BOX, ()) == Resources(cpu=16)
    assert _admit(FOREIGN, 4, lease=whole) == Admission(admitted=True, on_loan=9)
    assert _admit(HOLDER, 16, lease=whole) == Admission(admitted=True)


@pytest.mark.parametrize(
    ('cpu', 'free', 'expected'),
    [
        (4, 4, ()),  # fits already: nothing
        (6, 0, ('n',)),  # the newest loan suffices
        (12, 0, ('n', 'o')),
        (13, 0, None),  # CONTROL: even every loan is not enough
    ],
)
def test_which_loans_to_preempt(cpu: float, free: float, expected: tuple[str, ...] | None) -> None:
    loans = (_loan(6, minutes=60, sha='o'), _loan(6, minutes=5, sha='n'), Held(Resources(cpu=4), FOREIGN))
    got = preempt(Resources(cpu=cpu), Resources(cpu=free), loans, (LEASE.id,))
    assert (None if got is None else tuple(one.job.sha[0] for one in got if one.job)) == expected


def test_claimable_marks_loans_and_drops_what_a_holder_preempts() -> None:
    loan = _loan(12, minutes=5)
    got = claimable(
        [(HOLDER, Resources(cpu=8)), (FOREIGN, Resources(cpu=4))], BOX, reservations=(LEASE,), running=(loan,), now=NOW
    )
    assert (got[0][0], got[0][1].preempt) == (HOLDER, (loan,))
    assert got[1][1] == Admission(admitted=True)  # the preempted loan's room, beside the holder's 8
