"""The WAIT policy itself: bounded, visible, and refusing with a NAME.

The cross-process proof that the exclusion is real is
``tests/test_two_repos_cannot_both_hold_the_box.py``. This file is about the three properties that
sit ON TOP of the exclusion and that a subprocess test would only prove slowly:

* the wait has a CEILING and honours it, so a held box cannot turn a run into a hang;
* the wait SAYS SOMETHING while it waits, so a queue is distinguishable from a hang at a terminal;
* the refusal NAMES the holder, so its reader can act instead of guessing.

The broker is INJECTED rather than mocked away: every take below is a real atomic take against a
real record root, and what is planted is only who already holds it.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

import pytest

from lab_commons.dev import boxwait
from lab_commons.dev.boxlock import BOX_POOL, BoxLock
from lab_commons.dev.boxwait import PROGRESS_S, WAIT_S, hold_the_box, holders_line
from lab_commons.resources import Basis, Exhausted

#: A wait short enough that the ceiling can be MEASURED inside a test, and long enough to contain
#: several polls. It is the ratio that matters -- the test asserts the wait stops near this value,
#: never that a particular number of seconds elapsed.
_WAIT_S = 1.0
_POLL_S = 0.05

#: The slack allowed around the ceiling: one poll plus the cost of a take. An absolute bound here is
#: a statement about a BUSY box, so it is generous in the direction that cannot produce a false red.
_SLACK_S = 5.0


@pytest.fixture(autouse=True)
def _own_record_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here rations a PRIVATE box, so a real gate on this machine is neither seen nor held."""
    monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path))


def test_the_default_ceiling_is_the_gate_tier_bound() -> None:
    """The two constants are the declaration, so they are read rather than assumed."""
    assert WAIT_S == 1800.0, 'the wait ceiling is the 30 minutes this family already bounds a gate by'
    assert 0 < PROGRESS_S < WAIT_S, 'a progress interval at or above the ceiling would never print'


def test_an_idle_box_is_taken_without_waiting() -> None:
    """THE FLOOR. A wait that refused unconditionally would pass every assertion below it."""
    with contextlib.ExitStack() as stack:
        hold_the_box(stack, 'the only run', wait_s=_WAIT_S, poll_s=_POLL_S)
        assert [holder.what for holder in BoxLock.holders()] == ['the only run']
    assert BoxLock.holders() == (), 'the seat is released when the stack unwinds, however it unwinds'


def test_a_held_box_refuses_on_the_ceiling_naming_the_holder() -> None:
    """THE CHECK: the wait is BOUNDED, and what comes out of it can be acted on."""
    with contextlib.ExitStack() as held:
        hold_the_box(held, 'the run that got there first', wait_s=0.0, poll_s=_POLL_S)
        started = time.monotonic()
        with contextlib.ExitStack() as queued, pytest.raises(Exhausted) as refusal:
            hold_the_box(queued, 'the run behind it', wait_s=_WAIT_S, poll_s=_POLL_S)
        waited = time.monotonic() - started

    assert _WAIT_S <= waited < _WAIT_S + _SLACK_S, (
        f'the wait took {waited:.2f}s against a {_WAIT_S}s ceiling. Below it the ceiling is not '
        f'honoured; far above it the wait is not bounded by the thing that claims to bound it.'
    )
    assert 'the run that got there first' in holders_line(refusal.value), (
        'the refusal must NAME the holder: "busy" sends its reader to the process table to guess, '
        "and guessing wrong kills somebody else's evidence"
    )


def test_the_wait_announces_the_holder_while_it_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SILENT QUEUE IS A HANG, as far as anyone watching a terminal can tell.

    ``PROGRESS_S`` is lowered so several intervals fit inside a one-second wait -- the property
    under test is that the loop announces REPEATEDLY and names the holder, not that 60 is 60, which
    the constants test above pins separately.
    """
    said: list[str] = []
    monkeypatch.setattr(boxwait, 'PROGRESS_S', 0.1)
    monkeypatch.setattr(boxwait, 'emit', lambda line, **_: said.append(line))
    with contextlib.ExitStack() as held:
        hold_the_box(held, 'the long solve', wait_s=0.0, poll_s=_POLL_S)
        with contextlib.ExitStack() as queued, pytest.raises(Exhausted):
            hold_the_box(queued, 'the queued verify', wait_s=_WAIT_S, poll_s=_POLL_S)

    assert len(said) >= 2, f'a queue that speaks once and then goes quiet reads as a hang: {said}'
    assert all('the long solve' in line for line in said), f'every line must name the holder: {said}'
    assert all(line.startswith('the queued verify') for line in said), f'and name the waiter: {said}'


def test_an_unnamed_holder_still_produces_an_actionable_line() -> None:
    """``holders_line`` may never return an empty string: a blank refusal names nothing at all."""
    nameless = Exhausted(
        BOX_POOL,
        'box_seats',
        needed=1,
        available=0,
        basis=Basis.CONSERVATIVE_DEFAULT,
        what='a run with no visible peer',
    )
    assert holders_line(nameless).strip(), 'a refusal with no readable holder still has to say something'
