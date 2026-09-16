"""A record must be PUBLISHED, even while a peer holds the destination open to read it.

THE OTHER HALF OF THE HAZARD ``3509d2a`` FIXED. That commit made the RELEASE insist: on Windows an
open handle refuses the ``unlink`` underneath it, so a release that met a reader leaked a seat naming
a LIVE pid. The WRITE has the same exposure and the opposite consequence. ``_records.publish`` stages
a temp file and RENAMES it into place, and Windows refuses a rename whose DESTINATION a reader holds
open::

    PermissionError: [WinError 5] Access is denied:
      '...\\staged-nzittwzn' -> '...\\lab-commons-box.claim.39816.31904.json'

MEASURED 2026-09-16 from motronics-studio's take-loop arm, a child process storming reads while the
parent took and released the box in a loop. The claim file's name is stable per (pid, thread), so
the second iteration's publish lands on the first iteration's file -- which the storming reader had
open.

WHY IT IS WORSE THAN THE LEAK. A leaked seat starves a taker, but it refuses CLEANLY and the refusal
names a holder. This one raised ``PermissionError`` out of ``BoxLock.held()``, so a caller that
handles ``Exhausted`` -- the refusal this package documents -- got an unhandled OS error instead. A
reader merely LOOKING at the lock could crash a writer.

TWO CONTROLS, BOTH ON THE REAL FUNCTION. The PLANTED one refuses ``os.replace`` the way Windows
refuses it, and runs everywhere. The REAL one opens the destination and publishes over it with the
handle live: it reproduces the measured defect on Windows and is trivially satisfied on POSIX, which
is exactly the asymmetry that kept this hazard out of sight until a Windows box ran the loop.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import lab_commons._records as records
from lab_commons._records import publish
from lab_commons.dev.boxlock import BoxLock
from lab_commons.resources import Broker, CapacityRegistry


def test_a_publish_that_meets_a_transient_refusal_still_lands(tmp_path: Path, monkeypatch) -> None:
    """PLANTED CONTROL. The first renames fail exactly as an open handle makes them fail."""
    path = tmp_path / 'pool.claim.1.2.json'
    path.write_text('{"pool": "old"}', encoding='utf-8')
    refusals = {'left': 3}
    real_replace = os.replace

    def flaky(src, dst, **kwargs):
        if Path(dst) == path and refusals['left']:
            refusals['left'] -= 1
            msg = 'the process cannot access the file because it is being used by another process'
            raise PermissionError(msg)
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, 'replace', flaky)
    assert publish(path, {'pool': 'new'}) is True, 'a publish gave up while a reader was merely mid-read'
    assert refusals['left'] == 0, 'FLOOR: the planted refusals never fired, so nothing was tested'
    assert 'new' in path.read_text(encoding='utf-8')


def test_a_publish_that_can_NEVER_land_REPORTS_rather_than_raising(tmp_path: Path, monkeypatch) -> None:
    """THE OTHER SIDE. A permanent refusal is a ``False``, not an ``OSError`` escaping to a caller.

    The seat was never taken, so there is nothing to undo and nothing to leak; what there IS, is a
    caller that was told ``Exhausted`` is how this package refuses. An ``OSError`` out of a take is a
    second failure mode nobody was told about.
    """
    path = tmp_path / 'pool.claim.1.2.json'

    def always(*_args, **_kwargs):
        msg = 'permanently locked'
        raise PermissionError(msg)

    monkeypatch.setattr(os, 'replace', always)
    assert publish(path, {'pool': 'new'}) is False
    assert list(tmp_path.iterdir()) == [], 'a failed publish left its staging file behind'


def test_an_UNCONTESTED_publish_neither_sleeps_nor_fails(tmp_path: Path, monkeypatch) -> None:
    """RATCHET, second side: the common case must not pay for the rare one.

    A retry loop that sleeps unconditionally is a regression on EVERY take, so the floor is that
    nothing here reaches ``time.sleep`` when the first rename succeeds.
    """

    def forbidden(_seconds: float) -> None:
        msg = 'an uncontested publish slept'
        raise AssertionError(msg)

    monkeypatch.setattr(records.time, 'sleep', forbidden)
    path = tmp_path / 'pool.claim.1.2.json'
    assert publish(path, {'pool': 'new'}) is True
    assert publish(path, {'pool': 'newer'}) is True, 'an UPDATE over an existing record must land too'
    assert 'newer' in path.read_text(encoding='utf-8')


def test_a_publish_lands_while_a_READER_HOLDS_THE_DESTINATION_OPEN(tmp_path: Path) -> None:
    """THE REAL CONTROL, no plant: the measured defect, reproduced through the real function.

    On Windows this RAISED ``PermissionError`` before the fix. On POSIX a rename over an open file
    has always been legal, so this arm is satisfied trivially there -- and that platform split is
    the whole reason the hazard reached a release.

    THE READER LETS GO, AND THAT IS THE HONEST STATEMENT OF WHAT THE FIX BUYS. Written first with a
    handle held for the whole call, this arm stayed RED after the fix and was right to: a bounded
    retry cannot outwait an unbounded hold, and nothing short of a lease could -- which is the one
    thing this package will not do, because a lease decides a live holder is stale. What insistence
    covers is the case that was actually measured: a peer PARSING a record, which owns the handle
    for microseconds. So the reader here holds it briefly and releases, and the ceiling is stated
    rather than hidden: a hold longer than ``_INSIST_ATTEMPTS * _INSIST_BACKOFF_S`` still reports.
    """
    path = tmp_path / 'pool.claim.1.2.json'
    publish(path, {'pool': 'first'})
    opened = threading.Event()

    def reader() -> None:
        with path.open(encoding='utf-8') as stream:
            stream.read()
            opened.set()
            time.sleep(0.05)  # well inside the insistence window, and far past one bare attempt

    hand = threading.Thread(target=reader, name='holds-the-record-open')
    hand.start()
    try:
        assert opened.wait(5.0), 'FLOOR: the reader never opened the record, so nothing was contested'
        assert publish(path, {'pool': 'second'}) is True
    finally:
        hand.join(5.0)
    assert 'second' in path.read_text(encoding='utf-8')


def test_the_box_is_TAKEABLE_IN_A_LOOP_while_a_reader_storms_it(tmp_path: Path) -> None:
    """THE PROPERTY THE MEASUREMENT WAS ABOUT, driven through the REAL ``BoxLock``.

    The claim file's name repeats per (pid, thread), so iteration N publishes onto iteration N-1's
    file. A poll storm holding that file open is what turned a take into an ``OSError``.
    """
    broker = Broker(CapacityRegistry(), resource_dir=tmp_path)
    stop = threading.Event()

    def storm() -> None:
        while not stop.is_set():
            BoxLock.holders(broker=broker)

    hand = threading.Thread(target=storm, name='the-poll-storm', daemon=True)
    hand.start()
    try:
        for index in range(20):
            with BoxLock(f'take {index}', broker=broker).held():
                pass
    finally:
        stop.set()
        hand.join(5.0)
    assert BoxLock.holders(broker=broker) == (), 'a seat survived the loop and names this live process'
