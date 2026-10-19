"""Admit a run, then run it INSIDE a kernel limit sized exactly to its claim.

This is what makes the ledger's promise true: a claim reserves a :class:`Demand`, and the process it
was admitted for cannot use more -- the memory is a hard limit on the whole process tree, the CPU a
hard rate cap, and the wall clock ends the tree. The row is released when the tree is gone, so a
claim is never idle-but-held and a held claim is never exceeded.

ENFORCERS, per platform (:attr:`ProcessEnd.enforcer` names which one held the run):

* Windows -- a Job Object (:mod:`lab_commons.box._win`).
* Linux -- a transient systemd scope (:mod:`lab_commons.box._linux`); inside a Slurm job's cgroup
  the launcher watches the run's group itself, and the run says so. Anything else REFUSES:
  an unenforced run would make the ledger a list of hopes.
* macOS -- refused.

LEARNING IS THE CALLER'S. The box knows nothing of repos or producers and must not import
:mod:`lab_commons.compute`; the caller records :class:`ProcessEnd`'s measurements (``record_run``), and a
``memory`` outcome is the caller's cue to grow the next demand.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from lab_commons.box import _linux, _win
from lab_commons.box.demand import Capacity, Demand, ProcessEnd, Refused
from lab_commons.box.ledger import CPU_ENV, INSIDE_ENV, Read, adopted, hold
from lab_commons.box.supply import Reading

__all__ = ['BLAS_THREAD_VARS', 'command', 'run']

#: The runtimes that build one thread pool per PROCESS behind numpy/scipy, read at IMPORT time, so they must be
#: in the environment the child is LAUNCHED with. Unset, each sizes its pool to the whole box, and on Windows --
#: where the box limits COMMIT -- OpenBLAS reserves its buffers per thread at import: 756 MB per ``import numpy``
#: on a 32-thread host, 17 MB at one thread. :func:`run` sets them to the claim's cores: declared == used.
BLAS_THREAD_VARS: tuple[str, ...] = (
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
)

#: One backend per platform that has one: ``contain(argv, demand, cwd, env, (key, fingerprint)) -> ProcessEnd``.
Contain = Callable[[Sequence[str], Demand, str | None, Mapping[str, str] | None, tuple[str, str | None]], ProcessEnd]
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
    read: Read = Reading.now,
    claim: int = 0,
) -> ProcessEnd:
    """Admit *demand* (waiting at most *queue_s*), run *argv* enforced to it, release, report.

    A nonzero *claim* is a ledger row the caller already :func:`~lab_commons.box.ledger.take`-s at exactly
    *demand*: the run ADOPTS it (:func:`~lab_commons.box.ledger.adopted`) and is never admitted a second time.

    *key* is the caller's opaque identity of WHAT runs (a test id, a case+solver+mesh+analysis) and
    *fingerprint* its hash of the code the run depends on. The box never parses either: both go on the
    ledger row and come back on the :class:`ProcessEnd`, so the caller can learn per key (same key and
    fingerprint: a strong reference; another fingerprint: a weak one; a killed run: a lower bound).

    Raises:
        Refused: no room in time, or no enforcer on this machine (never an unenforced run).

    """
    if not argv:
        msg = f'{what}: an empty argv names nothing to run'
        raise ValueError(msg)
    if demand.cpu < 1 or demand.mem_gb <= 0:
        msg = f'{what}: a run is enforced to positive cores and memory, not a seat-only claim'
        raise ValueError(msg)
    contain = _enforcer()
    cores = str(demand.cpu)
    pools = dict.fromkeys(BLAS_THREAD_VARS, cores)
    inside = {**(os.environ if env is None else env), **pools, INSIDE_ENV: key, CPU_ENV: cores}
    held = (
        adopted(claim)
        if claim
        else hold(demand, what, queue_s, capacity=capacity, read=read, key=key, fingerprint=fingerprint)
    )
    with held:
        return contain(argv, demand, cwd, inside, (key, fingerprint))


def command(
    argv: Sequence[str],
    demand: Demand,
    queue_s: float,
    *,
    key: str,
    what: str,
    outcome: Path,
    fingerprint: str | None = None,
    capacity: Capacity | None = None,
    claim: int = 0,
) -> list[str]:
    """*argv* spelled as ``python -m lab_commons.box run ... -- argv``: the CLI that runs it enforced to *demand*.

    For a caller that streams the child's output itself: both streams stay the child's and so does the
    exit code. How the BOX ended it is the record at *outcome*, read back with
    :func:`~lab_commons.box.demand.read_ended` -- never the exit code, which the child may set to anything.

    *capacity* is the size the caller sized *demand* against, handed to the child (``--capacity``) so it holds
    against that ONE reading and never re-derives a size from its own cwd or machine file; ``None`` lets the
    child read this box (:meth:`Capacity.read`). A nonzero *claim* (``--claim``) is the row the child adopts.
    """
    seats = [f'--seat={name}={count}' for name, count in sorted(demand.seats.items()) if count]
    marks = ['--fingerprint', fingerprint] if fingerprint else []
    marks += ['--claim', str(claim)] if claim else []
    marks += ['--capacity', json.dumps(asdict(capacity), sort_keys=True)] if capacity is not None else []
    limits = ['--cpu', str(demand.cpu), '--mem-gb', f'{demand.mem_gb:g}', '--wall', str(demand.wall_s)]
    return [
        sys.executable, '-m', 'lab_commons.box', 'run', *limits, '--queue-s', f'{queue_s:g}', *seats,
        '--key', key, *marks, '--what', what, '--outcome', str(outcome), '--', *argv,
    ]  # fmt: skip


def _enforcer() -> Contain:
    contain = _ENFORCERS.get(sys.platform)
    why = f'box: no enforcer on {sys.platform}; refusing to run unenforced' if contain is None else None
    if sys.platform.startswith('linux'):
        why = _linux.refusal()
    if contain is None or why is not None:
        raise Refused(why)
    return contain
