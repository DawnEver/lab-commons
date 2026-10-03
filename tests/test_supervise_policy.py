"""The three brakes, and the sentences that say which one held."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from lab_commons.supervise.policy import Decision, decide
from lab_commons.supervise.state import new_state

_START = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _config(**alerts: object) -> dict[str, Any]:
    base: dict[str, Any] = {'suppress_after_identical': 3, 'cooldown': 600}
    base.update(alerts)
    return {'alerts': base}


def _at(minutes: int) -> datetime:
    return _START + timedelta(minutes=minutes)


def test_a_first_sighting_is_always_told() -> None:
    """Something new is the one case no brake applies to."""
    decision = decide(new_state(), 'probe.down', 'sha-a', _config(), clock=_at(0))
    assert decision.notify is True
    assert decision.sightings == 1


def test_a_changed_signature_releases_suppression() -> None:
    """THE BRAKE THAT MATTERS: a condition that moved must not hide behind one that did not."""
    state = new_state()
    for minute in range(0, 60, 10):
        decide(state, 'probe.down', 'sha-a', _config(), clock=_at(minute))
    moved = decide(state, 'probe.down', 'sha-b', _config(), clock=_at(60))
    assert moved.notify is True
    assert moved.sightings == 1, 'a new signature is a new count'


def test_an_identical_repeat_within_the_cooldown_is_held() -> None:
    """A supervisor failing for an hour has told you already."""
    state = new_state()
    decide(state, 'probe.down', 'sha-a', _config(), clock=_at(0))
    again = decide(state, 'probe.down', 'sha-a', _config(), clock=_at(1))
    assert again.notify is False
    assert 'cooldown' in again.reason


def test_an_identical_repeat_after_the_cooldown_is_told_once_more() -> None:
    """Persistent but not yet written off: worth repeating at the configured pace."""
    state = new_state()
    decide(state, 'probe.down', 'sha-a', _config(), clock=_at(0))
    later = decide(state, 'probe.down', 'sha-a', _config(), clock=_at(11))
    assert later.notify is True


def test_the_repeats_stop_once_the_condition_is_written_off() -> None:
    """Continuing to send it trains a reader to ignore the channel."""
    state = new_state()
    decision = Decision(notify=False, reason='')
    for minute in range(0, 200, 11):
        decision = decide(state, 'probe.down', 'sha-a', _config(), clock=_at(minute))
    assert decision.notify is False
    assert 'silent' in decision.reason


def test_escalation_overrides_the_write_off() -> None:
    """Persistence is itself the problem, and this is how a write-off is deliberately lifted."""
    state = new_state()
    decision = Decision(notify=False, reason='')
    for minute in range(0, 200, 11):
        decision = decide(state, 'probe.down', 'sha-a', _config(), clock=_at(minute), escalate_after=5)
    assert decision.notify is True
    assert 'unresolved' in decision.reason


def test_the_reason_names_the_brake_that_held() -> None:
    """`no alert` and `no supervisor` look the same from outside, so the reason is load-bearing."""
    state = new_state()
    decide(state, 'probe.down', 'sha-a', _config(), clock=_at(0))
    held = decide(state, 'probe.down', 'sha-a', _config(), clock=_at(1))
    told = decide(new_state(), 'probe.down', 'sha-a', _config(), clock=_at(0))
    assert held.reason != told.reason
    assert 'cooldown' in held.reason


def test_two_sources_are_judged_apart() -> None:
    """One loud component must not exhaust another's cooldown."""
    state = new_state()
    config = _config()
    decide(state, 'a.down', 'sha-a', config, clock=_at(0))
    decide(state, 'b.down', 'sha-b', config, clock=_at(0))
    assert decide(state, 'a.down', 'sha-a', config, clock=_at(1)).notify is False
    assert decide(state, 'b.down', 'sha-b', config, clock=_at(1)).notify is False
    other = decide(state, 'c.down', 'sha-c', config, clock=_at(1))
    assert other.notify is True


def test_a_state_that_has_never_been_written_still_decides() -> None:
    """A first run has no history, and must start rather than refuse."""
    assert decide({}, 'probe.down', 'sha-a', _config(), clock=_at(0)).notify is True


def test_an_unreadable_last_alert_timestamp_does_not_hold_forever() -> None:
    """A corrupt stamp must not become a permanent silence; absence of evidence is not a brake."""
    state = new_state()
    state['anomalies']['probe.down'] = {
        'consecutive': 1,
        'signature': 'sha-a',
        'notified': 2,
        'last_alert': 'not a timestamp',
    }
    assert decide(state, 'probe.down', 'sha-a', _config(), clock=_at(0)).notify is True
