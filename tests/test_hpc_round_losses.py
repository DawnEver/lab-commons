"""A ROUND SAYS WHAT IT LOST: every failed, out-of-memory or timed-out id with its kind, peak and wall.

User ruling 2026-10-31: the cluster being unreachable is normal, so a run publishes what each round learned
(``state['rounds'][n]['lost']``, carried by the record) instead of leaving it in streams only a shell reads.
"""

from __future__ import annotations

from lab_commons.compute.measured import Measured
from lab_commons.hpc.records import fold, round_losses

OOM = (
    '{"id": "a.py::ok", "status": "ok", "s": 1}\n{"start": "a.py::hog"}\n'
    '{"done": -9, "wall": 28.0, "peak_mb": 4102.2}\n'
)
SLOW = '{"start": "b.py::slow"}\n'  # never closed: the shard's wall cut it
FAILED = (
    '{"id": "c.py::bad", "status": "rejected", "reason": {"kind": "assertion", "text": "assert 1 == 2"}, "s": 3.5}\n'
    '{"done": 1, "wall": 4.0, "peak_mb": 300.0}\n'
)
CLEAN = '{"id": "d.py::fine", "status": "ok", "s": 2}\n{"done": 0, "wall": 2.5, "peak_mb": 250.0}\n'
ITEMS = [{'ids': ['a.py::ok', 'a.py::hog', 'a.py::after']}, {'ids': ['b.py::slow']}, {'ids': ['c.py::bad']}]


def test_a_round_with_an_oom_a_timeout_and_a_failure_names_all_three() -> None:
    lost = round_losses(
        ITEMS,
        {0: OOM, 1: SLOW, 2: FAILED},
        shards=[(0, 1), (1, 2), (2, 3)],
        walls=[30.0, 3600.0, 5.0],
        states={0: 'OUT_OF_MEMORY', 1: 'TIMEOUT', 2: 'COMPLETED'},
    )
    by_id = {one['id']: one for one in lost}
    assert set(by_id) == {'a.py::hog', 'b.py::slow', 'c.py::bad'}, 'the passed id and the re-queued one are no loss'
    assert (by_id['a.py::hog']['kind'], by_id['a.py::hog']['peak_mb'], by_id['a.py::hog']['wall_s']) == (
        'out_of_memory',
        4102.2,
        28.0,
    )
    assert '1 id(s) after it re-queued' in by_id['a.py::hog']['why']
    assert (by_id['b.py::slow']['kind'], by_id['b.py::slow']['wall_s']) == ('timeout', 3600.0)
    assert (by_id['c.py::bad']['kind'], by_id['c.py::bad']['why'], by_id['c.py::bad']['wall_s']) == (
        'rejected',
        'assert 1 == 2',
        3.5,
    )


def test_a_clean_round_loses_nothing() -> None:
    """PLANTED CONTROL: the same reader over a round that passed publishes no loss."""
    lost = round_losses([{'ids': ['d.py::fine']}], {0: CLEAN}, shards=[(0, 1)], walls=[3.0], states={0: 'COMPLETED'})
    assert lost == []


def test_an_expected_failure_is_no_loss() -> None:
    xfail = (
        '{"id": "e.py::known", "status": "rejected", "reason": {"kind": "expected", "text": "residual 3%"},'
        ' "expectation": {"status": "rejected", "why": "residual 3%"}, "s": 1}\n{"done": 0, "wall": 1, "peak_mb": 1}\n'
    )
    assert round_losses([{'ids': ['e.py::known']}], {0: xfail}, shards=[(0, 1)], walls=[1.0], states={}) == []


def test_round_losses_agrees_with_what_fold_re_queues() -> None:
    """The ids a kill cut are the ones fold re-queues: the loss names the cut, the rest follow it."""
    streams = {0: OOM, 1: SLOW, 2: FAILED}
    pending, killed, _ = fold(ITEMS, streams, {}, Measured.of({}), shards=[(0, 1), (1, 2), (2, 3)], walls=[30, 3600, 5])
    assert killed
    requeued = {n for part in pending for n in part}
    lost = round_losses(ITEMS, streams, shards=[(0, 1), (1, 2), (2, 3)], walls=[30, 3600, 5], states={})
    assert {one['id'] for one in lost if one['kind'] != 'rejected'} <= requeued


_LIMIT_MB = 4096.0
_CRASH = (
    '{"id": "f.py::ok", "status": "ok", "s": 1}\n{"start": "f.py::crash"}\n'
    '{"done": -11, "wall": 3.0, "peak_mb": 375.0}\n'
)
_NEAR = '{"start": "f.py::ok"}\n{"done": -9, "wall": 3.0, "peak_mb": 4000.0}\n'
_FOUR = [{'ids': ['f.py::ok', 'f.py::crash', 'f.py::x', 'f.py::y']}]


def test_a_low_peak_signal_is_the_tests_own_crash_decided_and_never_bisected() -> None:
    """Measured 2026-11-23: a test that faults its own item at 375 MB was bisected as out_of_memory for 4 rounds."""
    cells: dict[str, dict[str, object]] = {}
    pending, killed, _ = fold(
        _FOUR, {0: _CRASH}, cells, Measured.of({}), shards=[(0, 1)], walls=[5.0], limit_mb={0: _LIMIT_MB}
    )
    assert cells['f.py::crash']['status'] == 'rejected'
    reason = cells['f.py::crash']['reason']
    assert reason['kind'] == 'killed'
    assert 'signal 11' in reason['text']
    assert '375 MB' in reason['text']
    assert pending == [['f.py::x', 'f.py::y']], 'the cut id is decided; the ids after it re-run whole'
    assert not killed
    lost = round_losses(_FOUR, {0: _CRASH}, shards=[(0, 1)], walls=[5.0], states={}, limit_mb={0: _LIMIT_MB})
    assert [(one['id'], one['kind']) for one in lost] == [('f.py::crash', 'killed')]


def test_a_near_limit_signal_keeps_the_out_of_memory_bisection() -> None:
    """PLANTED CONTROL: the same signal at a peak near the limit is still memory, re-queued and halved."""
    cells: dict[str, dict[str, object]] = {}
    pending, killed, _ = fold(
        _FOUR, {0: _NEAR}, cells, Measured.of({}), shards=[(0, 1)], walls=[5.0], limit_mb={0: _LIMIT_MB}
    )
    assert cells == {}
    assert pending == [['f.py::ok', 'f.py::crash'], ['f.py::x', 'f.py::y']]
    assert killed
    lost = round_losses(_FOUR, {0: _NEAR}, shards=[(0, 1)], walls=[5.0], states={}, limit_mb={0: _LIMIT_MB})
    assert [(one['id'], one['kind']) for one in lost] == [('f.py::ok', 'out_of_memory')]
