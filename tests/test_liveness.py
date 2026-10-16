"""The liveness oracle, and the TWO INCIDENTS it has to satisfy at once.

They point in OPPOSITE directions and the danger is fixing either alone:

* 2026-09-02 -- a dead holder's recycled pid blocked every gate on the box: a bare ``pid_alive`` on a
  recorded number. The box ledger records the creation stamp beside the pid; ``test_box.py``'s
  ``test_a_dead_launchers_row_is_reaped`` plants the recycled pid.
* 2026-09-03 -- a seat was released when the python CLIENT exited while the job it drove ran on for an
  hour. The ledger hands the claim to the job (``lab_commons.box.hand_over``); ``test_box.py``'s
  ``test_a_claim_handed_to_a_vendor_lives_and_is_reaped_with_it`` pins it.

What stays here is the oracle's own conservative direction.
"""

from __future__ import annotations

import os

from lab_commons.liveness import still_the_same_process

_STAMP = 111
_OTHER_STAMP = 222


def test_an_unreadable_stamp_cannot_disprove_an_identity() -> None:
    """``None`` from the clock means STILL HELD; a differing stamp is another process."""
    pid = os.getpid()
    assert still_the_same_process(pid, _STAMP, alive=lambda _p: True, clock=lambda _p: None)
    assert still_the_same_process(pid, None, alive=lambda _p: True, clock=lambda _p: _OTHER_STAMP)
    assert not still_the_same_process(pid, _STAMP, alive=lambda _p: True, clock=lambda _p: _OTHER_STAMP)
    assert not still_the_same_process(pid, _STAMP, alive=lambda _p: False, clock=lambda _p: _STAMP)
