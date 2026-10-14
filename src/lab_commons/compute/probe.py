"""THE PROBES -- what a host or a Slurm account has NOW, read from the system and returned as a :class:`Supply`.

Three sources, one value (:mod:`lab_commons.compute.model`):

* **box**: what the box ledger can still admit (:mod:`lab_commons.box`, the one source of a box's resources):
  its :class:`~lab_commons.box.Capacity` (cores and memory after the ``[reserve]`` margins, the declared tool
  seats) less every claim it holds now -- an interactive verify, a producer, anything run through
  :func:`lab_commons.box.run` -- and the OS itself (``windows``, ``linux``, ``macos``) with count = ``cpu``.
* **tools**: the machine file's ``[capabilities.<tool>]`` declares ``seats = N`` -- the one number a person
  declares, because no probe can read a licence. A tool is present when its probe (:data:`TOOL_PROBES`) finds
  it installed, or, without a probe, when it is declared.
* **slurm account**: ``cpu`` AND ``mem_gb`` over EVERY grant of the account (:func:`account`), plus ``submits``
  -- the job submissions its QOS still accepts (infinite when uncapped) -- and ``files``, what the home's
  FILE quota still takes (user ruling 2026-10-14: an Ada home at its 2,020,000-file limit failed every
  write while its supply read free cores; the caller reads it, :mod:`lab_commons.dev.diskusage`).

The readers of the OS are module functions taken as defaults, so a test passes fakes; the arithmetic is pure.
"""

from __future__ import annotations

import math
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

from lab_commons.box import GB, Capacity, Holding
from lab_commons.compute.model import Resources, Supply
from lab_commons.hpc.plan import headroom
from lab_commons.hpc.slurm import Snapshot

if sys.platform == 'win32':
    import winreg

__all__ = [
    'OS_NAMES',
    'TOOL_PROBES',
    'account',
    'box',
    'os_name',
    'tools',
]

#: ``sys.platform`` -> the resource name of the OS.
OS_NAMES: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}


def os_name(platform: str = sys.platform) -> str:
    """The OS resource of *platform*; an OS the family does not serve is refused by name."""
    if platform not in OS_NAMES:
        msg = f'no OS resource for sys.platform {platform!r}; known: {sorted(OS_NAMES)}'
        raise ValueError(msg)
    return OS_NAMES[platform]


# --- tools --------------------------------------------------------------------------------------------


def _com_registered(*progids: str) -> Callable[[], bool]:
    def probe() -> bool:
        if sys.platform != 'win32':
            return False
        try:
            for progid in progids:
                winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'{progid}\\CLSID').Close()
        except OSError:
            return False
        return True

    return probe


def _on_path(program: str) -> Callable[[], bool]:
    return lambda: shutil.which(program) is not None


#: Tool -> whether it is installed here. A tool without a probe is present when declared.
TOOL_PROBES: Final[Mapping[str, Callable[[], bool]]] = {
    'jmag': _com_registered('designer.Application', 'scheduler.JobApplication'),
    'matlab': _on_path('matlab'),
    'node': _on_path('node'),
}


def tools(
    capabilities: Mapping[str, Mapping[str, Any]],
    *,
    running: Iterable[frozenset[str]] = (),
    probes: Mapping[str, Callable[[], bool]] = TOOL_PROBES,
) -> tuple[Resources, frozenset[str]]:
    """``(counts, present)`` of the declared tools: ``seats`` (default 1) less the *running* need-sets using each.

    A declared tool its probe does not find is ABSENT, whatever its seats say.
    """
    held = list(running)
    present = frozenset(name for name in capabilities if name not in probes or probes[name]())
    counts = {
        name: max(0, int(capabilities[name].get('seats', 1)) - sum(name in needs for needs in held)) for name in present
    }
    return Resources(counts), present


# --- the two kinds of supply ------------------------------------------------------------------------------


def box(
    scope: str,
    *,
    capacity: Capacity,
    holdings: Sequence[Holding] = (),
    platform: str = sys.platform,
    probes: Mapping[str, Callable[[], bool]] = TOOL_PROBES,
) -> Supply:
    """The box now: its *capacity* less every claim in *holdings* (the box ledger), its OS and its tools."""
    cpu = max(0.0, capacity.cpu - sum(one.cpu for one in holdings))
    mem_gb = max(0, capacity.mem_bytes - sum(one.mem_bytes for one in holdings)) / GB
    system = os_name(platform)
    declared = {name: {'seats': seats} for name, seats in capacity.seats.items()}
    _, present = tools(declared, probes=probes)
    counts = Resources({
        name: max(0, capacity.seats[name] - sum(one.seats.get(name, 0) for one in holdings)) for name in present
    })  # fmt: skip
    measured = Resources({'cpu': cpu, 'mem_gb': mem_gb, system: cpu}) + counts
    return Supply(scope, measured, present | measured.names | {system})


def account(
    scope: str,
    snapshots: Sequence[tuple[Snapshot, str]],
    *,
    files: float = math.inf,
) -> Supply:
    """A Slurm account over EVERY grant: ``(snapshot, qos)`` each, summed; ``submits`` infinite if uncapped.

    *files* is the home's file-quota headroom (limit less used): at the limit it is 0, and a run -- every
    run creates files -- is refused naming ``files``. Infinite only where no file quota exists.

    The account's OS, ``linux``, counts as many as ``cpu``.
    ``cpu`` per grant is :func:`lab_commons.hpc.plan.headroom` (quota less what is held); ``mem_gb`` the
    quota's remaining memory, else -- uncapped -- the free memory of the schedulable nodes.
    """
    cpu = mem = submits = 0.0
    for snapshot, qos in snapshots:
        cpu += headroom(snapshot)
        left = snapshot.quota.remaining()['mem']
        mem += (left if left is not None else sum(n.mem_mb for n in snapshot.nodes if n.schedulable)) / 1024
        room = snapshot.quota.submit_room(qos)
        submits += math.inf if room is None else room
    linux = OS_NAMES['linux']  # a Slurm account runs on linux: the OS is a resource there like any other
    measured = Resources({'cpu': cpu, 'mem_gb': mem, 'submits': submits, 'files': files, linux: cpu})
    return Supply(scope, measured, frozenset({'cpu', 'mem_gb', 'submits', 'files', linux}))
