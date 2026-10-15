"""Admit a run, then run it INSIDE a kernel limit sized exactly to its claim.

This is what makes the ledger's promise true: a claim reserves a :class:`Demand`, and the process it
was admitted for cannot use more -- the memory is a hard limit on the whole process tree, the CPU a
hard rate cap, and the wall clock ends the tree. The row is released when the tree is gone, so a
claim is never idle-but-held and a held claim is never exceeded.

ENFORCERS, per platform (:attr:`Outcome.enforcer` names which one held the run):

* Windows -- a Job Object (:mod:`lab_commons.box._win`).
* Linux -- a transient systemd scope (:mod:`lab_commons.box._linux`); inside a Slurm job's cgroup
  the launcher watches the run's group itself, and the run says so. Anything else REFUSES:
  an unenforced run would make the ledger a list of hopes.
* macOS -- refused.

LEARNING IS THE CALLER'S. The box knows nothing of repos or producers and must not import
:mod:`lab_commons.compute`; the caller records :class:`Outcome`'s measurements (``record_run``), and a
``memory`` outcome is the caller's cue to grow the next demand.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from lab_commons.box import _linux, _win
from lab_commons.box.demand import Capacity, Demand, Outcome, Refused
from lab_commons.box.ledger import admit

__all__ = ['INSIDE_ENV', 'KILLED', 'REFUSED', 'command', 'run']

#: Set on every boxed child to its run's key: a tree already inside a claim is not claimed twice.
INSIDE_ENV: Final = 'LAB_COMMONS_BOXED'

#: The CLI's exit code when the box refused the run (no room in time, or no enforcer): sysexits EX_UNAVAILABLE.
REFUSED: Final = 69

#: The CLI's exit code when the box killed the tree (memory or wall): 128 + SIGKILL, the code a shell expects.
KILLED: Final = 137

#: One backend per platform that has one: ``contain(argv, demand, cwd, env, (key, fingerprint)) -> Outcome``.
Contain = Callable[[Sequence[str], Demand, str | None, Mapping[str, str] | None, tuple[str, str | None]], Outcome]
_ENFORCERS: dict[str, Contain] = {'win32': _win.contain, 'linux': _linux.contain}


def run(
    argv: Sequence[str],
    demand: Demand,
    what: str,
    queue_s: float,
    *,
    key: str,
    fingerprint: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    capacity: Capacity | None = None,
) -> Outcome:
    """Admit *demand* (waiting at most *queue_s*), run *argv* enforced to it, release, report.

    *key* is the caller's opaque identity of WHAT runs (a test id, a case+solver+mesh+analysis) and
    *fingerprint* its hash of the code the run depends on. The box never parses either: both go on the
    ledger row and come back on the :class:`Outcome`, so the caller can learn per key (same key and
    fingerprint: a strong reference; another fingerprint: a weak one; a killed run: a lower bound).

    Raises:
        Refused: no room in time, or no enforcer on this machine (never an unenforced run).

    """
    if not argv:
        msg = f'{what}: an empty argv names nothing to run'
        raise ValueError(msg)
    contain = _enforcer()
    inside = {**(os.environ if env is None else env), INSIDE_ENV: key}
    with admit(demand, what, queue_s, capacity=capacity, key=key, fingerprint=fingerprint):
        return contain(argv, demand, cwd, inside, (key, fingerprint))


def command(
    argv: Sequence[str], demand: Demand, queue_s: float, *, key: str, what: str, fingerprint: str | None = None
) -> list[str]:
    """*argv* spelled as ``python -m lab_commons.box run ... -- argv``: the CLI that runs it enforced to *demand*.

    For a caller that streams the child's output itself: stdout stays the child's, the
    :class:`~lab_commons.box.demand.Outcome` is one JSON line on stderr, and the exit code is the child's,
    :data:`KILLED` or :data:`REFUSED`.
    """
    seats = [f'--seat={name}={count}' for name, count in sorted(demand.seats.items()) if count]
    marks = ['--fingerprint', fingerprint] if fingerprint else []
    limits = ['--cpu', str(demand.cpu), '--mem-gb', f'{demand.mem_gb:g}', '--wall', str(demand.wall_s)]
    return [
        sys.executable, '-m', 'lab_commons.box', 'run', *limits, '--queue-s', f'{queue_s:g}', *seats,
        '--key', key, *marks, '--what', what, '--', *argv,
    ]  # fmt: skip


def _enforcer() -> Contain:
    contain = _ENFORCERS.get(sys.platform)
    why = f'box: no enforcer on {sys.platform}; refusing to run unenforced' if contain is None else None
    if sys.platform.startswith('linux'):
        why = _linux.refusal()
    if contain is None or why is not None:
        raise Refused(why)
    return contain
