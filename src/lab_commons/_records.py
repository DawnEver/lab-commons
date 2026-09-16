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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lab_commons.liveness import CreationClock, creation_stamp

# The annotation only: importing `resources` at runtime here would be the cycle this split made.
if TYPE_CHECKING:
    from lab_commons.resources import JobHandle

__all__ = ['publish', 'record', 'stage', 'unpublish']

#: How hard a record operation INSISTS against a reader's open handle, and how long it pauses
#: between tries. ONE POLICY, BOTH DIRECTIONS -- applied through :func:`_insist`.
#:
#: WHY A RECORD OPERATION CAN FAIL AT ALL, and why it is Windows that made it matter. A reader opens
#: a record to parse it; on Windows an open handle refuses both the unlink underneath it AND a
#: rename onto it (`FILE_SHARE_DELETE` is not what CPython's `open` asks for).
#:
#: A RELEASE that met a mid-read reader left a seat naming a pid that is genuinely ALIVE. It reads
#: as HELD -- the conservative direction everywhere else, and here starvation: the process that just
#: released is refused by its own leaked record for the rest of its life.
#:
#: A PUBLISH that met one raised `PermissionError` out of the take, which is WORSE. The leak refuses
#: cleanly and names a holder; this one is an `OSError` no caller of this package was told to expect,
#: so a reader merely LOOKING at a lock could crash a writer.
#:
#: MEASURED 2026-09-16, driving motronics' gate lock (a `BoxLock` adapter) through a take/release
#: loop beside a concurrent poll storm: the loop wedged on its own seat within a few seconds, and
#: with that fixed the next iteration's claim rename raised instead. A reader holds the file for
#: microseconds, so insisting for a fraction of a second is the whole fix -- and it is a RETRY
#: rather than a lease, so nothing here consults a clock about a holder.
_INSIST_ATTEMPTS: Final = 40
_INSIST_BACKOFF_S: Final = 0.005

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


def _insist(operation: Callable[[], object], *, missing: bool) -> bool:
    """Run *operation*, retrying briefly against the refusal a reader's open handle produces.

    ONE SPELLING FOR ONE POLICY, and that is the reason this exists rather than two loops. The
    release half of this hazard was fixed on its own, the publish half was found unfixed a day
    later, and a second hand-rolled retry beside the first is how the pair drifts into two
    different answers to the same question about the same directory.

    BOUNDED, AND A RETRY RATHER THAN A LEASE. It gives up after ``_INSIST_ATTEMPTS`` and says so;
    it never decides a holder is stale, which is the one judgement this package refuses to make
    from a clock. It does not sleep before the first try, so an UNCONTESTED operation -- every
    operation on an idle box -- costs exactly what it did before.

    *missing* is what ``FileNotFoundError`` MEANS for the caller, and it is terminal rather than
    retried: for an unlink the record is gone, which is success; for a rename the staged file this
    function was handed has vanished, which is not.
    """
    for attempt in range(_INSIST_ATTEMPTS):
        try:
            operation()
        except FileNotFoundError:
            return missing
        except OSError:
            if attempt + 1 == _INSIST_ATTEMPTS:
                return False
            time.sleep(_INSIST_BACKOFF_S)
        else:
            return True
    return False


def publish(path: Path, payload: Mapping[str, object]) -> bool:
    """Put *payload* at *path* ATOMICALLY: a reader sees the whole old record or the whole new.

    WHY NOT ``path.write_text``, which this module used. It opens with O_TRUNC, so every update
    emptied the file for the length of the write -- the same observable state the acquisition path
    used to leave, and the same state a peer reads as "nobody is here". An update is now as
    invisible-until-complete as a create.

    RETURNS WHETHER THE RECORD LANDED, and does not raise when it did not. Windows refuses a rename
    whose destination a reader holds open, so the ordinary contested case used to throw
    ``PermissionError`` out of a take -- past a caller that had been told ``Exhausted`` is how this
    package refuses. A failed publish is not a leak and not a half-state: the staging file is
    removed, the old record is untouched, and NOTHING WAS TAKEN, so a boolean is a complete report
    and the caller is the only party that can price it.

    THE BOUNDARY IS THE RENAME, NOT THE WRITE. Only the contested step is softened; ``stage`` still
    raises, so a full disk, a bad directory or an unwritable root is reported as loudly as ever.

    WHAT A ``False`` MEANS AT THE ONE PLACE IT IS REACHABLE, and why it is not a refusal.
    :meth:`lab_commons.resources.Broker._write_claim` is the publish a taker does before it waits;
    its file's name repeats per (pid, thread), so take two in a loop renames onto take one's file --
    the one a polling peer has open, which is the measured case. A claim SHARPENS a headroom check
    and is never a seat: peers count it, the taker does not, the release path already tolerates its
    absence, and every seat is still arbitrated by ``os.link``. So a claim that did not land costs
    precision on ONE take, while refusing would spend an available box on a handle a reader drops in
    microseconds. Refusing as ``Exhausted`` would be worse than either: no dimension is full, and a
    refusal naming a cause that is not true sends its reader somewhere there is nothing to find.
    """
    staged = stage(path, payload)
    if _insist(lambda: os.replace(staged, path), missing=False):
        return True
    with contextlib.suppress(OSError):
        staged.unlink()
    return False


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
    return _insist(path.unlink, missing=True)
