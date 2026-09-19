"""``lab_commons.dev.famtests.boxseat`` -- every arm driven GREEN and, where reachable, driven RED.

NO REAL BOX SEAT IS TAKEN HERE. Every plant goes through ``Broker(resource_dir=tmp_path)``, which is
the same isolation the body itself documents: this suite runs inside a ``verify`` run that already
holds the real seat, so a case taking it would be refused by its own runner and a case releasing it
would hand the box to whoever is queued behind this process.

WHICH REDS ARE REACHABLE, AND THE ONE THAT IS NOT. The entry-point arm, the private-pool arm, the
rendezvous arm and the bounded-wait arm all take a per-repo input, so a wrong answer is plantable and
is planted below. The exclusion arms drive the REAL ``BoxLock``: their red is "the family's lock
stopped excluding", which cannot be planted without replacing the thing under test -- so what is
asserted for those is that the arm reaches its own green on a real lock, and their floor (a held box
that names its holder) is driven separately. Saying that here rather than inventing a fake lock is
the point: a control over a stub would prove the stub.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

from lab_commons.dev import verify
from lab_commons.dev.boxlock import BoxLock
from lab_commons.dev.boxwait import PROGRESS_S, WAIT_S
from lab_commons.dev.famtests.boxseat import (
    HOLD_THE_BOX_PARAMETERS,
    assert_a_pool_name_of_its_own_does_not_opt_a_run_out,
    assert_a_second_run_is_refused_while_one_holds_the_box,
    assert_asking_who_holds_the_box_is_not_taking_it,
    assert_the_box_pool_is_a_name_rather_than_a_measurement,
    assert_the_rendezvous_is_outside_this_repository,
    assert_the_seat_is_released_when_the_run_ends,
    assert_the_verdict_entry_point_takes_the_box,
    assert_the_wait_is_bounded_and_narrates,
)

#: The share the two labs both pin today, used as the argument this body takes rather than as a
#: constant it closes over. A consumer is free to pin tighter; it may not omit it.
_A_SHARE = 0.25


@pytest.fixture
def records(tmp_path: Path) -> Path:
    """An ISOLATED records directory, so every plant contends only with its own case."""
    directory = tmp_path / 'records'
    directory.mkdir()
    return directory


def test_this_kits_own_verdict_entry_point_takes_the_box() -> None:
    """GREEN on the real thing: ``run_verify`` is this repo's own answer to the argument."""
    assert_the_verdict_entry_point_takes_the_box(entry_point=verify.run_verify)


def test_an_entry_point_whose_module_never_bound_the_call_reds() -> None:
    """PLANTED: a verdict function in a module that does not participate at all."""

    def a_runner_that_takes_nothing() -> int:
        return 0

    with pytest.raises(AssertionError, match='as `hold_the_box`'):
        assert_the_verdict_entry_point_takes_the_box(entry_point=a_runner_that_takes_nothing)


def test_an_entry_point_that_imports_the_call_without_making_it_reds(monkeypatch) -> None:
    """PLANTED: the party-that-takes-none state -- the name is bound, and nothing calls it.

    This is the arm both forks were weakest on: asserting the IDENTITY alone passes on a module that
    imports ``hold_the_box`` and never takes the seat, which is exactly what a refactor leaves behind.
    """

    def a_runner_that_only_imports_it() -> int:
        return 0

    module = sys.modules[a_runner_that_only_imports_it.__module__]
    monkeypatch.setattr(module, 'hold_the_box', _the_family_function(), raising=False)
    with pytest.raises(AssertionError, match='no longer calls'):
        assert_the_verdict_entry_point_takes_the_box(entry_point=a_runner_that_only_imports_it)


def _the_family_function() -> object:
    """The very object the body compares identity against, fetched rather than re-imported by name."""
    return sys.modules['lab_commons.dev.boxwait'].hold_the_box


def test_a_second_run_is_refused_while_one_holds_the_box(records: Path) -> None:
    """Two brokers, one path, one seat -- driven on the REAL lock."""
    assert_a_second_run_is_refused_while_one_holds_the_box(records=records)


def test_the_seat_is_released_when_the_run_ends(records: Path) -> None:
    """THE OTHER SIDE. A seat nothing can take again is a hang wearing an exclusion."""
    assert_the_seat_is_released_when_the_run_ends(records=records)


def test_a_private_pool_does_not_opt_a_run_out_of_the_box(records: Path) -> None:
    """THE PATH: agreeing on the mechanism and naming your own pool measures nothing."""
    assert_a_pool_name_of_its_own_does_not_opt_a_run_out(
        records=records,
        pool_name='a-pool-of-its-own',
    )


def test_an_unnamed_private_pool_reds(records: Path) -> None:
    """THE FLOOR on that plant. A blank pool name plants nothing and could not fail."""
    with pytest.raises(AssertionError, match='plants nothing'):
        assert_a_pool_name_of_its_own_does_not_opt_a_run_out(records=records, pool_name='   ')


def test_asking_who_holds_the_box_is_not_taking_it(records: Path) -> None:
    """Asked twice on a fresh directory, because a read with a write in it shows on the second call."""
    assert_asking_who_holds_the_box_is_not_taking_it(records=records)


def test_the_rendezvous_is_outside_this_repository() -> None:
    """GREEN: the box records are not inside this checkout."""
    assert_the_rendezvous_is_outside_this_repository(root=Path(__file__).resolve().parents[1])


def test_a_rendezvous_inside_the_tree_reds() -> None:
    """PLANTED: ask the question rooted at the records directory itself, which contains itself.

    MEASURED BEFORE IT WAS WRITTEN, and the first attempt was WRONG in the silent direction: it
    planted the filesystem root of this source file -- one drive letter -- on the assumption that
    every path is relative to it. The box records live on ANOTHER drive, under TEMP, so it reached
    and the arm would have passed without ever convicting. A control on a drive the subject is not
    on is a control that cannot fail -- exactly the shape this whole package exists to refuse.
    """
    with pytest.raises(AssertionError, match='A rendezvous a sibling repo cannot reach'):
        assert_the_rendezvous_is_outside_this_repository(root=BoxLock.resource_dir())


def test_the_wait_is_bounded_and_narrates() -> None:
    """GREEN at the share both labs pin today."""
    assert_the_wait_is_bounded_and_narrates(progress_share_ceiling=_A_SHARE)


def test_a_share_that_is_not_a_fraction_reds() -> None:
    """A ceiling of 0 can never be met and one above 1 is no ceiling; both are refused."""
    for share in (0.0, 1.5, -0.25):
        with pytest.raises(AssertionError, match='not a fraction'):
            assert_the_wait_is_bounded_and_narrates(progress_share_ceiling=share)


def test_a_share_the_live_queue_misses_reds() -> None:
    """PLANTED: a share so tight the family's own progress interval fails it."""
    tight = (PROGRESS_S / WAIT_S) / 2
    with pytest.raises(AssertionError, match='past the declared share'):
        assert_the_wait_is_bounded_and_narrates(progress_share_ceiling=tight)


def test_the_parameter_pin_is_an_equality_not_a_superset(monkeypatch) -> None:
    """THE BEHAVIOURAL DIFFERENCE FROM BOTH FORKS, planted in the direction ``>=`` cannot see.

    A parameter ADDED to the shared wait satisfies ``parameters.keys() >= {...}`` and breaks every
    other repo's caller. Here it reds, and the message names the addition.
    """

    def hold_the_box_with_one_more(
        stack: contextlib.ExitStack,
        what: str,
        *,
        wait_s: float,
        poll_s: float,
        a_new_required_thing: str,
    ) -> None:
        _ = (stack, what, wait_s, poll_s, a_new_required_thing)

    monkeypatch.setattr('lab_commons.dev.famtests.boxseat.hold_the_box', hold_the_box_with_one_more)
    with pytest.raises(AssertionError, match='a_new_required_thing'):
        assert_the_wait_is_bounded_and_narrates(progress_share_ceiling=_A_SHARE)


def test_the_parameter_pin_is_the_live_signature() -> None:
    """FLOOR ON THE PIN. An empty set would make the equality above a statement about nothing."""
    assert frozenset({'stack', 'what', 'wait_s', 'poll_s'}) == HOLD_THE_BOX_PARAMETERS


def test_the_box_pool_is_a_name_rather_than_a_measurement() -> None:
    """FLOOR ON THE READING. An empty pool name would make every plant here address nothing."""
    assert_the_box_pool_is_a_name_rather_than_a_measurement()
