"""The reservation RECORD: its one shape, and the two writes that put it on disk atomically.

Split out of :mod:`lab_commons.resources` so the broker's file is about ADMISSION and this one is
about the bytes a peer reads. The seam is real rather than cosmetic: every correctness fix this
family has made to the record -- the staged-then-linked create, the staged-then-replaced update,
the ``created`` discriminator -- is a fix HERE, and each was found by a reader seeing a state the
writer did not mean to publish.

Private by name because the shape is not a contract. A reader that wants a holder asks
:meth:`lab_commons.resources.Broker.holders` and gets a :class:`~lab_commons.resources.Holder`; the
JSON keys are this package's business and may gain a field (``created`` did) without any consumer
being told.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lab_commons.liveness import CreationClock, creation_stamp

# The annotation only: importing `resources` at runtime here would be the cycle this split made.
if TYPE_CHECKING:
    from lab_commons.resources import JobHandle

__all__ = ['publish', 'record', 'stage', 'unpublish']

#: How hard a release INSISTS on removing its own record, and how long it pauses between tries.
#:
#: WHY A RELEASE CAN FAIL AT ALL, and why it is Windows that made it matter. A reader opens a record
#: to parse it; on Windows an open handle refuses the unlink underneath it (`FILE_SHARE_DELETE` is
#: not what CPython's `open` asks for), so a seat whose holder released while somebody was mid-read
#: survives -- naming a pid that is genuinely ALIVE. It then reads as HELD, which is the
#: conservative direction everywhere else and here is starvation: the process that just released is
#: refused by its own leaked record for the rest of its life.
#:
#: MEASURED 2026-09-16, driving motronics' gate lock (a `BoxLock` adapter) through a take/release
#: loop beside a concurrent poll storm: the loop wedged on its own seat within a few seconds. A
#: reader holds the file for microseconds, so insisting for a fraction of a second is the whole fix
#: -- and it is a RETRY rather than a lease, so nothing here consults a clock about a holder.
_UNLINK_ATTEMPTS: Final = 40
_UNLINK_BACKOFF_S: Final = 0.005

#: The prefix of a record being STAGED before it is published. It carries no dot, so it can never
#: match the ``{pool}.*`` scans a peer runs while looking for holders.
STAGED_PREFIX: Final = 'staged-'


def record(
    pool: str,
    what: str,
    job: JobHandle,
    demands: Mapping[str, int],
    observed_bytes: int | None,
    *,
    clock: CreationClock = creation_stamp,
) -> dict:
    """One holder's record: WHO holds it and what that holder declared.

    ONE WRITER FOR THE SHAPE. The file a peer reads a microsecond after the seat appears and the
    file it reads an hour later are published from this function, so the acquisition record and
    every later update cannot drift into two shapes a reader would have to know about.

    ``created`` IS THE PID'S IDENTITY and is written here BECAUSE this is the one writer. A pid
    alone is a number the OS recycles, and a reader that tests the number reports a dead holder as
    alive -- the 2026-09-02 incident :mod:`lab_commons.liveness` carries. ``None`` is written
    rather than omitted (an opaque handle, or a box with no readable stamp) so a reader can tell
    "no discriminator" from "a field this writer did not have".
    """
    pid = job.pid
    return {
        'pool': pool,
        'what': what,
        'job_kind': job.kind,
        'job_ident': job.ident,
        'created': None if pid is None else clock(pid),
        'demands': dict(demands),
        'since': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'observed_bytes': observed_bytes,
        'client_pid': os.getpid(),
    }


def stage(path: Path, payload: Mapping[str, object]) -> Path:
    """*payload* as JSON, in a temporary file IN *path*'s OWN directory.

    NOT in the system temp directory: publishing is a link and a replace, and neither can cross a
    filesystem -- the staged name and the published one have to be on the same one.
    """
    handle, staged = tempfile.mkstemp(dir=str(path.parent), prefix=STAGED_PREFIX)
    with os.fdopen(handle, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream)
    return Path(staged)


def publish(path: Path, payload: Mapping[str, object]) -> None:
    """Replace the record at *path* ATOMICALLY: a reader sees the whole old record or the whole new.

    WHY NOT ``path.write_text``, which this module used. It opens with O_TRUNC, so every update
    emptied the file for the length of the write -- the same observable state the acquisition path
    used to leave, and the same state a peer reads as "nobody is here". An update is now as
    invisible-until-complete as a create.
    """
    staged = stage(path, payload)
    try:
        os.replace(staged, path)
    except OSError:
        with contextlib.suppress(OSError):
            staged.unlink()
        raise


def unpublish(path: Path) -> bool:
    """Remove the record at *path*, INSISTING briefly against a reader's open handle.

    Returns ``True`` when the record is gone -- including when it was already gone, which is the
    ordinary outcome for a claim a peer reclaimed. ``False`` means a live record was left behind,
    and the caller is the only party that can say whether that matters; nothing is raised, because
    a release runs in a ``finally`` and an exception there would replace one leak with two.

    A SEAT IS NOT AN ORDINARY FILE, which is why this is not `contextlib.suppress(OSError)`. A
    leaked seat names a LIVE pid, so every later reader must treat it as a holder -- the failure is
    invisible, permanent for that process's lifetime, and points the wrong way from every other
    "cannot tell" in this package.
    """
    for attempt in range(_UNLINK_ATTEMPTS):
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 == _UNLINK_ATTEMPTS:
                return False
            time.sleep(_UNLINK_BACKOFF_S)
        else:
            return True
    return False
