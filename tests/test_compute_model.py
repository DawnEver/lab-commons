"""ONE ADMISSION: every resource equal, claim in rank order while it fits.

Each refusal below has its PLANTED CONTROL beside it: the same inputs with the one blocking fact removed admit.
"""

from __future__ import annotations

import pytest

from lab_commons.compute.admission import Admission, admit
from lab_commons.compute.model import (
    Demand,
    Job,
    Resources,
    Supply,
)

HOST = 'ws-01'

JMAG_JOB = Job('o/motor', 'a' * 40, 'pytest', frozenset({'jmag'}))
PLAIN_JOB = Job('o/kit', 'b' * 40, 'pytest', frozenset())


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
    got = admit(JMAG_JOB, Demand(cpu=2, jmag=1), _supply(cpu=8, jmag=1, windows=8))
    assert got == Admission(admitted=True)
    assert got
    assert got.why == ''


def test_a_missing_tool_is_named_as_lacking() -> None:
    got = admit(JMAG_JOB, Demand(cpu=1), _supply(cpu=8))
    assert not got
    assert got.lacking == ('jmag',)
    assert 'jmag' in got.why
    assert admit(JMAG_JOB, Demand(cpu=1), _supply(cpu=8, jmag=2))  # CONTROL


def test_a_short_resource_is_named_never_a_bare_false() -> None:
    got = admit(PLAIN_JOB, Demand(cpu=4, mem_gb=64), _supply(cpu=8, mem_gb=32))
    assert got.lacking == ('mem_gb',)


def test_running_work_is_subtracted() -> None:
    supply = _supply(cpu=8)
    assert not admit(PLAIN_JOB, Demand(cpu=4), supply, running=(Resources(cpu=6),))
    assert admit(PLAIN_JOB, Demand(cpu=4), supply, running=(Resources(cpu=4),))  # CONTROL
