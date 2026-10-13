"""ONE ADMISSION: every resource equal, reservations by scope and matcher, claim in rank order while it fits.

Each refusal below has its PLANTED CONTROL beside it: the same inputs with the one blocking fact removed admit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lab_commons.compute.model import (
    Admission,
    Demand,
    For,
    Job,
    Reservation,
    Resources,
    Supply,
    admit,
    claimable,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
HOST = 'ws-01'

JMAG_JOB = Job('o/motor', 'a' * 40, 'pytest', frozenset({'jmag'}), origin='alice')
PLAIN_JOB = Job('o/kit', 'b' * 40, 'pytest', frozenset(), origin='bob')


def _supply(**amounts: float) -> Supply:
    return Supply(HOST, Resources(amounts))


# --- Resources: one kind of thing, missing = 0 -------------------------------------------------------


def test_a_missing_name_is_zero_and_zero_is_not_kept() -> None:
    one = Resources({'cpu': 4, 'jmag': 0})
    assert one['femm'] == 0.0
    assert one.names == frozenset({'cpu'})
    assert one == Resources(cpu=4)


def test_a_negative_amount_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="'mem_gb'"):
        Resources(mem_gb=-1)


@pytest.mark.parametrize(
    ('left', 'right', 'total', 'rest'),
    [
        ({'cpu': 4}, {'cpu': 1, 'jmag': 1}, {'cpu': 5, 'jmag': 1}, {'cpu': 3}),
        ({'cpu': 1}, {'cpu': 4}, {'cpu': 5}, {}),  # subtraction floors at zero
        ({}, {}, {}, {}),
    ],
)
def test_add_and_floored_subtract(left: dict, right: dict, total: dict, rest: dict) -> None:
    assert Resources(left) + Resources(right) == Resources(total)
    assert Resources(left) - Resources(right) == Resources(rest)


def test_le_reads_every_name_of_the_left_side() -> None:
    assert Resources(cpu=2) <= Resources(cpu=2, jmag=1)
    assert not Resources(cpu=2, jmag=1) <= Resources(cpu=8)  # CONTROL: the tool is short, not the cpu
    assert Resources(cpu=2, jmag=1).short_of(Resources(cpu=8)) == ('jmag',)


def test_a_supply_counts_only_what_is_present() -> None:
    supply = Supply(HOST, Resources(cpu=8), listed=frozenset({'cpu', 'jmag'}))
    assert supply.present == frozenset({'cpu', 'jmag'})  # jmag installed, every seat in use
    with pytest.raises(ValueError, match="'femm'"):
        Supply(HOST, Resources(cpu=8, femm=1), listed=frozenset({'cpu'}))


# --- admit ---------------------------------------------------------------------------------------------


def test_a_fit_admits() -> None:
    got = admit(JMAG_JOB, Demand(cpu=2, jmag=1), _supply(cpu=8, jmag=1, windows=8), now=NOW)
    assert got == Admission(admitted=True)
    assert got
    assert got.why == ''


def test_a_missing_tool_is_named_as_lacking() -> None:
    got = admit(JMAG_JOB, Demand(cpu=1), _supply(cpu=8), now=NOW)
    assert not got
    assert got.lacking == ('jmag',)
    assert 'jmag' in got.why
    assert admit(JMAG_JOB, Demand(cpu=1), _supply(cpu=8, jmag=2), now=NOW)  # CONTROL


def test_a_short_resource_is_named_never_a_bare_false() -> None:
    got = admit(PLAIN_JOB, Demand(cpu=4, mem_gb=64), _supply(cpu=8, mem_gb=32), now=NOW)
    assert got.lacking == ('mem_gb',)


def test_running_work_is_subtracted() -> None:
    supply = _supply(cpu=8)
    assert not admit(PLAIN_JOB, Demand(cpu=4), supply, running=(Resources(cpu=6),), now=NOW)
    assert admit(PLAIN_JOB, Demand(cpu=4), supply, running=(Resources(cpu=4),), now=NOW)  # CONTROL


@pytest.mark.parametrize(
    ('matcher', 'blocks'),
    [
        (For(login='carol'), True),  # reserved for somebody else
        (For(login='bob'), False),  # the job's own login
        (For(repo='o/kit'), False),  # the job's own repo
        (For(repo='o/other'), True),
        (For(needs=frozenset()), False),  # every need-set contains the empty one
        (For(needs=frozenset({'jmag'})), True),
    ],
)
def test_a_reservation_blocks_everyone_it_is_not_for(matcher: For, *, blocks: bool) -> None:
    held = Reservation(HOST, Resources(cpu=6), matcher, until=LATER, by='ws-01-user')
    got = admit(PLAIN_JOB, Demand(cpu=4), _supply(cpu=8), reservations=(held,), running=(), now=NOW)
    assert bool(got) is not blocks
    if blocks:
        assert got.lacking == ('cpu',)


def test_an_expired_reservation_does_not_block() -> None:
    held = Reservation(HOST, Resources(cpu=6), For(login='carol'), until=NOW, by='ws-01-user')
    assert admit(PLAIN_JOB, Demand(cpu=4), _supply(cpu=8), reservations=(held,), running=(), now=NOW)
    assert not admit(
        PLAIN_JOB, Demand(cpu=4), _supply(cpu=8), reservations=(held,), running=(), now=NOW - timedelta(seconds=1)
    )  # CONTROL


def test_another_hosts_reservation_does_not_block() -> None:
    held = Reservation('hpc:acct', Resources(cpu=6), For(login='carol'), until=LATER, by='carol')
    assert admit(PLAIN_JOB, Demand(cpu=4), _supply(cpu=8), reservations=(held,), running=(), now=NOW)


def test_a_matcher_names_exactly_one_thing() -> None:
    with pytest.raises(ValueError, match='exactly one'):
        For(login='a', repo='o/r')
    with pytest.raises(ValueError, match='exactly one'):
        For()


# --- claimable -----------------------------------------------------------------------------------------


def _job(name: str) -> Job:
    return Job('o/r', name.ljust(40, '0'), 'pytest', frozenset())


def test_claim_in_rank_order_skipping_a_non_fit() -> None:
    """Rank order is authoritative; a job that does not fit is skipped and lower-ranked smaller ones still claim."""
    ranked = [
        (_job('a'), Demand(cpu=4)),
        (_job('big'), Demand(cpu=8)),
        (_job('c'), Demand(cpu=3)),
        (_job('d'), Demand(cpu=2)),
    ]
    got = claimable(ranked, _supply(cpu=8), now=NOW)
    assert [job.sha[:3] for job in got] == ['a00', 'c00']


def test_claim_counts_what_already_runs() -> None:
    ranked = [(_job('a'), Demand(cpu=4)), (_job('b'), Demand(cpu=4))]
    assert len(claimable(ranked, _supply(cpu=8), now=NOW)) == 2  # CONTROL
    assert len(claimable(ranked, _supply(cpu=8), running=(Resources(cpu=1),), now=NOW)) == 1
