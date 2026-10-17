"""Whether a recorded pid is STILL the process that was recorded.

THE TWO INCIDENTS THIS MODULE SITS BETWEEN, one day apart, pointing in OPPOSITE directions. Both
were real, both were measured, and any liveness test that adopts either one wholesale re-opens the
other.

* **2026-09-02 -- pid reuse.** consumer-a's ``scripts/gate/lock.py`` was written as an
  argument AGAINST recording a pid at all: a dead holder's pid was recycled by an unrelated ``cmd``
  process, the recorded number answered ALIVE, and every gate on the box was refused by a holder
  that had exited. Its remedy was a kernel byte-range lock, which the OS drops however the process
  ends and which therefore has no stale state to be wrong about.
* **2026-09-03 -- a job outliving its driver.** ``lab_commons.resources`` recorded ``os.getpid()``,
  the PYTHON CLIENT's id. A client gave up, its ``finally`` released the seat, and the vendor
  executable ran on for another hour with six live processes still eating the box. Its remedy was
  :class:`~lab_commons.resources.JobHandle` -- move the claim onto the JOB, which means recording a
  pid that is NOT this interpreter's. A kernel fd lock structurally cannot express that: the fd
  dies with the interpreter.

So the record must name a pid, and a pid is not an identity. THE DISCRIMINATOR IS THE CREATION
TIME. ``GetProcessTimes`` gives a process's creation FILETIME, which the OS never recycles with the
number: a pid whose creation stamp differs from the recorded one is a DIFFERENT process, whatever
the process table says about the number. That is the kernel fact ``lock.py`` was defending,
expressed as one record field and one comparison, and it leaves the outliving-exe case working.

THE UNKNOWN DIRECTION IS "STILL HELD", everywhere, and it is the same conservative direction
:func:`lab_commons.proc.descendants` already takes with the same stamp. A stamp that cannot be read
-- off Windows, or on a process this user may not open -- CANNOT DISPROVE the identity, so it does
not. Freeing a seat wrongly is the over-subscription the whole mechanism exists to prevent; refusing
one wrongly costs a wait. Only a stamp that is READ and DISAGREES is evidence, and only that frees
a seat.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import lab_commons.launch as _door
from lab_commons.proc import _creation_time, _kernel32, pid_alive

__all__ = ['SLACK_S', 'CreationClock', 'boot_time', 'creation_stamp', 'predates_boot', 'still_the_same_process']

#: Reads a pid's creation stamp, or ``None`` when it cannot. Injected wherever the comparison is
#: driven, so pid REUSE can be planted and the REAL guard called on a box that will not recycle a
#: pid to order -- and so the property is testable off Windows, where no stamp exists to compare.
CreationClock = Callable[[int], int | None]


def creation_stamp(pid: int) -> int | None:
    """*pid*'s creation FILETIME, or ``None`` when this box cannot say.

    The default :data:`CreationClock`. It is a thin name over
    :func:`lab_commons.proc._creation_time` rather than a second reader: ONE syscall wrapper, used
    both to refute a parent edge in :func:`~lab_commons.proc.descendants` and to refute an identity
    here, because two readers of one kernel fact drift on the first fix applied to either.
    """
    return _creation_time(pid)


def still_the_same_process(
    pid: int,
    created: int | None,
    *,
    alive: Callable[[int], bool] = pid_alive,
    clock: CreationClock = creation_stamp,
) -> bool:
    """Is *pid* running AND still the process whose creation stamp was *created*?

    Three answers, and the middle one is the whole point:

    * the pid is not in the process table -- gone, whatever else is true;
    * the pid is there and a stamp READ NOW DISAGREES with *created* -- a different process wearing
      a recycled number, which is the 2026-09-02 incident and is reported GONE;
    * anything else -- alive. That covers a record written before this field existed
      (``created is None``), a box whose stamps cannot be read (``clock`` returns ``None``), and the
      ordinary agreeing case. All three are "cannot disprove", and cannot-disprove is HELD.

    Args:
        pid: the operating-system process id the record names.
        created: the creation stamp recorded BESIDE that pid, or ``None`` if none was.
        alive: the process-table probe. Injected for the same reason as *clock*.
        clock: reads the stamp of the pid AS IT IS NOW, for comparison against *created*.

    Returns:
        ``True`` when the recorded job must still be treated as holding whatever it held.

    """
    if not alive(pid):
        return False
    if created is None:
        return True
    now = clock(pid)
    return now is None or now == created


def boot_time() -> float | None:
    """Epoch seconds of the last boot, or ``None`` when it cannot be read.

    Windows: now minus ``GetTickCount64`` (counts across sleep, so a resume is not a boot; jitters by
    milliseconds). Linux: ``/proc/stat`` ``btime``. macOS: ``sysctl kern.boottime``.
    """
    if os.name == 'nt':
        return _windows()
    if sys.platform == 'darwin':
        return _darwin()
    return _procfs()


def _windows() -> float | None:
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    try:
        tick = kernel32.GetTickCount64
        tick.restype = ctypes.c_ulonglong
        return time.time() - tick() / 1000.0
    except (AttributeError, OSError):
        return None


def _procfs() -> float | None:
    try:
        text = Path('/proc/stat').read_text(encoding='utf-8')
    except OSError:
        return None
    found = re.search(r'^btime (\d+)$', text, re.MULTILINE)
    return float(found.group(1)) if found else None


def _darwin() -> float | None:
    try:
        text = _door.run(
            ['/usr/sbin/sysctl', '-n', 'kern.boottime'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
            timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r'sec\s*=\s*(\d+)', text)
    return float(found.group(1)) if found else None


#: How far before the measured boot a file must be to predate it: Windows derives boot as now minus
#: uptime, so the instant jitters, and the slack keeps that jitter on the "not debris" side.
SLACK_S = 5.0


def predates_boot(path: Path) -> bool:
    """Whether *path* was last written before this box booted, so its writer is dead; ``False`` when unknown."""
    booted = boot_time()
    try:
        return booted is not None and path.stat().st_mtime < booted - SLACK_S
    except OSError:
        return False
