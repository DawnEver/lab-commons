"""Admit a run, then run it INSIDE a kernel limit sized exactly to its claim.

This is what makes the ledger's promise true: a claim reserves a :class:`Demand`, and the process it
was admitted for cannot use more -- the memory is a hard limit on the whole process tree, the CPU a
hard rate cap, and the wall clock ends the tree. The row is released when the tree is gone, so a
claim is never idle-but-held and a held claim is never exceeded.

ENFORCERS, per platform (:attr:`Outcome.enforcer` names which one held the run):

* Windows -- a Job Object (:mod:`lab_commons.box._win`).
* Linux -- a transient systemd ``--user`` scope (:mod:`lab_commons.box._linux`); on a Slurm compute
  node the allocation's own cgroup already enforces, and the run says so. Anything else REFUSES:
  an unenforced run would make the ledger a list of hopes.
* macOS -- refused.

LEARNING IS THE CALLER'S. The box knows nothing of repos or producers and must not import
:mod:`lab_commons.compute`; the caller records :class:`Outcome`'s measurements (``record_run``), and a
``memory`` outcome is the caller's cue to grow the next demand.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence

from lab_commons.box import _linux, _win
from lab_commons.box.demand import Capacity, Demand, Outcome, Refused
from lab_commons.box.ledger import admit

__all__ = ['run']

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
    with admit(demand, what, queue_s, capacity=capacity, key=key, fingerprint=fingerprint):
        return contain(argv, demand, cwd, env, (key, fingerprint))


def _enforcer() -> Contain:
    contain = _ENFORCERS.get(sys.platform)
    why = f'box: no enforcer on {sys.platform}; refusing to run unenforced' if contain is None else None
    if sys.platform.startswith('linux'):
        why = _linux.refusal()
    if contain is None or why is not None:
        raise Refused(why)
    return contain
