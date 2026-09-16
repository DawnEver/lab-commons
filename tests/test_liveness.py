"""The liveness oracle, and the TWO INCIDENTS it has to satisfy at once.

They are one file because they point in OPPOSITE directions and the danger is fixing either alone.
Each test below names its date and says what it refutes:

* ``test_a_recycled_pid_does_not_hold_the_seat_2026_09_02`` -- the incident that argues AGAINST
  recording a pid. A dead holder's number was recycled by an unrelated process and blocked every
  gate on the box. A bare ``pid_alive`` on a recorded number is exactly that probe, and it is what
  this broker did until the ``created`` stamp landed beside the pid.
* ``test_a_job_outliving_its_client_still_holds_the_seat_2026_09_03`` -- the incident that argues
  FOR recording a pid, one day later. The seat was released when the python CLIENT exited while the
  job it drove ran on for an hour. A kernel fd lock, which is what the first incident's remedy was,
  structurally cannot express that: the fd dies with the interpreter.

REVERT THE ``created`` FIELD AND THE FIRST TEST REDS while the second stays green -- which is the
check the taste page prescribes, and the reason both are here rather than one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lab_commons.liveness import still_the_same_process
from lab_commons.resources import BOX_SEATS, Broker, Capacity, CapacityRegistry, Exhausted, JobHandle

#: Two stamps that disagree. Values, not measurements: the point is the COMPARISON, and a test that
#: waited for this box to recycle a pid would never run.
_STAMP = 111
_OTHER_STAMP = 222

#: How long the outliving worker lives. Long enough that the client's death and the parent's read
#: both land inside it, short enough that a crashed test leaves nothing interesting behind.
_WORKER_LIFETIME_S = 30


def _broker(root: Path, stamp: int | None) -> Broker:
    """A one-seat broker over *root* whose creation clock always answers *stamp*."""
    registry = CapacityRegistry([('lane', Capacity.structural(BOX_SEATS.name, 1))])
    return Broker(registry, resource_dir=root, creation_clock=lambda _pid: stamp)


def _abandoned_seats(root: Path) -> tuple[Path, ...]:
    """The seat files exactly as a KILLED holder leaves them: real records, no release.

    Taken through the real admission path so the bytes are the ones the product writes, then put
    back after the grant unlinked them. A one-seat demand takes TWO indexed seats -- the pool's and
    the box-scoped one -- and both are restored, because restoring only one would leave the pool
    free and test nothing about the box.

    The records name THIS interpreter, which is alive, so the only thing that can tell the holder
    from a live one is the creation stamp. That is the point.
    """
    with _broker(root, _STAMP).admit('lane', {BOX_SEATS.name: 1}, what='the run that died'):
        seats = sorted(root.glob('*.slot'))
        assert {path.name for path in seats} == {'box_seats.0.slot', 'lane.seats.0.slot'}, (
            f'the planted set is the subject of this test; found {[path.name for path in seats]}'
        )
        content = {path: path.read_bytes() for path in seats}
    for path, payload in content.items():
        path.write_bytes(payload)
    return tuple(content)


def test_a_recycled_pid_does_not_hold_the_seat_2026_09_02(tmp_path: Path) -> None:
    """INCIDENT 2026-09-02, planted through the REAL broker: a recycled number is not a holder.

    What is planted is the ONE fact this box will not produce to order -- the recorded pid
    answering a DIFFERENT creation stamp than the record carries -- and the guard called is the
    real read-and-admit path over a real abandoned record.
    """
    seats = _abandoned_seats(tmp_path)
    recycled = _broker(tmp_path, _OTHER_STAMP)
    assert recycled.holders('lane') == [], (
        'a pid whose creation stamp disagrees with the record is a DIFFERENT process wearing a '
        'recycled number, and reading it as a holder is the 2026-09-02 incident'
    )
    with recycled.admit('lane', {BOX_SEATS.name: 1}, what='the gate the box blocked'):
        assert all(path.exists() for path in seats), 'the indexes are RECLAIMED, not merely ignored'


def test_the_recycled_pid_control_has_its_floor(tmp_path: Path) -> None:
    """THE FLOOR for the test above, over the SAME planted record: an AGREEING stamp is still held.

    Without it, a ``still_the_same_process`` that answered ``False`` unconditionally would pass the
    incident test -- and would free every live seat on the box.
    """
    _abandoned_seats(tmp_path)
    live = _broker(tmp_path, _STAMP)
    assert [holder.what for holder in live.holders('lane')] == ['the run that died']
    with pytest.raises(Exhausted, match='the run that died'), live.admit('lane', {BOX_SEATS.name: 1}, what='a peer'):
        pass  # reached only if the one-seat exclusion has gone


def test_a_job_outliving_its_client_still_holds_the_seat_2026_09_03(tmp_path: Path) -> None:
    """INCIDENT 2026-09-03, with REAL processes: the claim is on the JOB, not on the interpreter.

    A worker is started, a SEPARATE short-lived python takes the seat and moves the claim onto that
    worker's pid, and then that python is killed outright -- no ``finally``, no release, which is
    what a client that gives up actually does. The worker is still running, so the seat is still
    held, and it is released only when the WORKER goes.

    The ``created`` stamp added for the 2026-09-02 incident must not disturb this, which is the
    whole reason the two tests sit together: the worker's stamp still agrees with its record.
    """
    worker = subprocess.Popen([sys.executable, '-c', f'import time; time.sleep({_WORKER_LIFETIME_S})'])
    try:
        client = subprocess.run(
            [sys.executable, '-c', _CLIENT, str(tmp_path), str(worker.pid)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert client.returncode == 0, f'the planted client failed: {client.stdout}{client.stderr}'

        broker = Broker(resource_dir=tmp_path)
        holders = broker.holders('lane')
        assert [holder.job for holder in holders] == [JobHandle.for_pid(worker.pid)], (
            f'the seat must be held by the WORKER after its client exited; holders: '
            f'{[holder.describe() for holder in holders]}'
        )
    finally:
        worker.kill()
        worker.wait(timeout=30)

    deadline = time.monotonic() + 30
    while Broker(resource_dir=tmp_path).holders('lane') and time.monotonic() < deadline:
        time.sleep(0.2)
    assert Broker(resource_dir=tmp_path).holders('lane') == [], (
        'the seat is released when the JOB goes -- a holder that survives its worker is the '
        'immortal record the creation stamp exists to prevent'
    )


#: The planted client: take the seat, move the claim onto the worker, and DIE WITHOUT RELEASING.
#: ``os._exit`` skips every ``finally`` in the interpreter, which is what makes this the incident
#: rather than a polite shutdown.
_CLIENT = """
import os, sys
from lab_commons.resources import BOX_SEATS, Broker, Capacity, CapacityRegistry, JobHandle
root, worker = sys.argv[1], int(sys.argv[2])
registry = CapacityRegistry([('lane', Capacity.structural(BOX_SEATS.name, 1))])
broker = Broker(registry, resource_dir=__import__('pathlib').Path(root))
entered = broker.admit('lane', {BOX_SEATS.name: 1}, what='a vendor run')
grant = entered.__enter__()
grant.track(JobHandle.for_pid(worker))
sys.stdout.flush()
os._exit(0)
"""


def test_an_unreadable_stamp_cannot_disprove_an_identity() -> None:
    """The conservative direction, stated as a unit: ``None`` from the clock means STILL HELD.

    Off Windows there is no stamp to read, and on Windows a process this user may not open answers
    nothing. Either way the record's identity is UNDISPROVEN, and freeing a seat on an absence is
    the over-subscription the whole mechanism exists to prevent.
    """
    pid = os.getpid()
    assert still_the_same_process(pid, _STAMP, alive=lambda _p: True, clock=lambda _p: None)
    assert still_the_same_process(pid, None, alive=lambda _p: True, clock=lambda _p: _OTHER_STAMP)
    assert not still_the_same_process(pid, _STAMP, alive=lambda _p: True, clock=lambda _p: _OTHER_STAMP)
    assert not still_the_same_process(pid, _STAMP, alive=lambda _p: False, clock=lambda _p: _STAMP)
