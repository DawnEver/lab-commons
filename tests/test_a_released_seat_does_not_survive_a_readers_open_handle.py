"""A release must REMOVE its seat, even while a peer holds the file open to read it.

WHAT WAS MEASURED, 2026-09-16. motronics-studio's `scripts/gate/lock.py` became an adapter over
`BoxLock`, and the first thing its own suite did was drive take-and-release in a loop beside a
concurrent poll storm -- the shape two agents polling `--who cpu` produce on a real box. Within
seconds the loop was refused by a holder named `a real run [client:37504]`: its OWN pid, from an
iteration it had already left. A reader had the seat file open when the release tried to unlink it,
Windows refused the unlink under the open handle, and `contextlib.suppress(OSError)` made that
silent.

WHY THAT IS WORSE THAN AN ORDINARY LEAK, and why the direction is the opposite of every other
"cannot tell" in `resources.py`. A leftover seat names a pid that is genuinely ALIVE, so
`Broker._job_alive` answers correctly that the job is running and `_take_indexed` cannot reclaim the
index. The record is indistinguishable from a live claim by construction. It is not stale data to be
detected; it is a claim nobody made, and it outlives the run that leaked it for the whole lifetime
of the process.

THE FIX IS INSISTENCE, NOT A LEASE. A reader holds a record open for microseconds, so retrying the
unlink for a fraction of a second closes it without anything in this package consulting a clock
about whether a holder is alive.

THE CONTROL IS PLANTED ON THE REAL GUARD. A Windows open handle cannot be arranged portably, so the
refusal is planted in the FILESYSTEM CALL -- an `unlink` that fails the way Windows fails it -- and
the real `unpublish` is called. The two-sided arm is the one that matters: a `unpublish` that gave
up immediately must RED here, which is the state this file was written against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons._records import unpublish
from lab_commons.dev.boxlock import BoxLock
from lab_commons.resources import Broker, CapacityRegistry, Exhausted


def test_a_release_that_meets_a_transient_refusal_still_removes_the_record(tmp_path: Path, monkeypatch) -> None:
    """PLANTED CONTROL. The first unlinks fail exactly as an open handle makes them fail."""
    seat = tmp_path / 'box_seats.0.slot'
    seat.write_text('{}', encoding='utf-8')
    refusals = {'left': 3}
    real_unlink = Path.unlink

    def flaky(self: Path, *args, **kwargs):
        if self == seat and refusals['left']:
            refusals['left'] -= 1
            msg = 'the process cannot access the file because it is being used by another process'
            raise PermissionError(msg)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', flaky)
    assert unpublish(seat) is True, 'a release gave up while a reader was merely mid-read'
    assert refusals['left'] == 0, 'FLOOR: the planted refusals never fired, so nothing was tested'
    assert not seat.exists()


def test_a_release_that_can_NEVER_remove_the_record_says_so_rather_than_raising(tmp_path: Path, monkeypatch) -> None:
    """THE OTHER SIDE. A release runs in a `finally`; raising there would replace one leak with two.

    So a permanent refusal is REPORTED, never raised and never silently read as success -- the two
    wrong answers this function sits between.
    """
    seat = tmp_path / 'box_seats.0.slot'
    seat.write_text('{}', encoding='utf-8')

    def always(*_args, **_kwargs):
        msg = 'permanently locked'
        raise PermissionError(msg)

    monkeypatch.setattr(Path, 'unlink', always)
    assert unpublish(seat) is False


def test_a_MISSING_record_is_a_successful_release(tmp_path: Path) -> None:
    """A peer that already reclaimed the index leaves nothing to remove, and that is not a failure."""
    assert unpublish(tmp_path / 'never-existed.slot') is True


def test_the_box_is_TAKEABLE_AGAIN_after_a_release_that_met_a_refusal(tmp_path: Path, monkeypatch) -> None:
    """THE PROPERTY THE MEASUREMENT WAS ABOUT, driven through the REAL `BoxLock`.

    The arms above are about one function. This one is about the box: take it, release it while the
    filesystem refuses the first unlinks, and take it AGAIN. Before the fix the second take was
    refused by the first take's own record, with this process named as the live holder.
    """
    broker = Broker(CapacityRegistry(), resource_dir=tmp_path)
    real_unlink = Path.unlink
    refusals = {'left': 2}

    def flaky(self: Path, *args, **kwargs):
        if self.suffix == '.slot' and refusals['left']:
            refusals['left'] -= 1
            msg = 'the process cannot access the file because it is being used by another process'
            raise PermissionError(msg)
        return real_unlink(self, *args, **kwargs)

    with BoxLock('the first run', broker=broker).held():
        pass
    monkeypatch.setattr(Path, 'unlink', flaky)
    with BoxLock('the run whose release is refused', broker=broker).held():
        pass
    assert refusals['left'] == 0, 'FLOOR: the planted refusals never fired, so nothing was tested'

    assert BoxLock.holders(broker=broker) == (), 'a released seat survived and names this live process'
    with BoxLock('the run that must not be refused by a ghost', broker=broker).held():
        pass


def test_a_LIVE_holder_still_excludes_so_the_fix_did_not_delete_the_mechanism(tmp_path: Path) -> None:
    """RATCHET, second side. Insisting harder on a release must not loosen the take."""
    broker = Broker(CapacityRegistry(), resource_dir=tmp_path)
    with (
        BoxLock('a run that is really going', broker=broker).held(),
        pytest.raises(Exhausted),
        BoxLock('the impatient one', broker=broker).held(),
    ):
        pytest.fail('two holders of a one-seat box')
