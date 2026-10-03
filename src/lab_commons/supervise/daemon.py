"""The always-on loop: run a cycle, wait, run another, until told to stop.

A SUPERVISOR DOES NOT MANAGE ITSELF. There is no pid file here and no self-restart ladder, because
the kit's own rule is that a second lock is a second definition of the one that exists, and because
a process supervised by a process manager has no business supervising itself. Under systemd the
unit carries ``Restart=always``; the liveness question is answered by the manager, and it is asked
of the OS rather than of a timestamp this module wrote down.

WHAT THIS DOES OWN is the two things a loop must get right and a timer cannot: it does not overlap
itself, and it stops when asked. A cycle that finds the seat taken SKIPS rather than fails, because
the other cycle is the one doing the work. A signal sets a flag and the cycle in flight finishes
first, so a restart never lands mid-remedy -- a systemd restart that interrupted a deployment would
leave the target in a state no later cycle knows about.

WHAT IT DOES NOT DO IS DECIDE WHERE RECORDS GO. The loop hands each cycle's result to a callback
and the entry point chooses the journal, which keeps the digest's writer in one place instead of
having this module grow an opinion about files.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, Final

from lab_commons.log import emit
from lab_commons.resources import Broker, Exhausted
from lab_commons.supervise.alert import Notifier
from lab_commons.supervise.component import Registry
from lab_commons.supervise.loop import CycleResult, run_cycle
from lab_commons.supervise.policy import now
from lab_commons.supervise.process.base import ProcessManager
from lab_commons.supervise.report import summary
from lab_commons.supervise.state import StateStore

__all__ = ['MIN_INTERVAL', 'Loop', 'serve']

#: The shortest interval a loop will honour. Anything smaller is a supervisor competing with the
#: service it supervises, which on a two-core host is the service losing.
MIN_INTERVAL: Final = 5

#: What a cycle's result is handed to, for a caller that wants to record it.
Watcher = Callable[[CycleResult, datetime], None]


@dataclass
class Loop:
    """Everything one running supervisor holds.

    Attributes:
        registry: the components, actions and chains.
        store: where state lives.
        config: the merged configuration.
        project: the target's directory.
        manager: how a remedy reaches the service's process.
        broker: where a cycle's seat comes from. Defaults to the shared broker.
        notifier: how a notice is delivered. Defaults to one over the merged config.
        stop: set to ask the loop to finish the cycle in flight and return.

    """

    registry: Registry
    store: StateStore
    config: dict[str, Any]
    project: Path
    manager: ProcessManager
    broker: Broker | None = None
    notifier: Notifier | None = None
    stop: threading.Event = field(default_factory=threading.Event)

    def interval(self) -> int:
        """Return how long to wait between cycles.

        Returns:
            The configured interval, floored at :data:`MIN_INTERVAL`.

        """
        return max(MIN_INTERVAL, int(self.config.get('cycle', {}).get('interval', 300)))

    def once(self, clock: datetime | None = None) -> CycleResult | None:
        """Run one cycle, or skip it because another holds the seat.

        Args:
            clock: the instant to judge this cycle at. Defaults to the real one.

        Returns:
            The cycle's result, or None when another cycle held the seat -- a skip is not a
            failure, because the other cycle is the one doing the work.

        """
        try:
            return run_cycle(
                self.registry,
                self.store,
                self.config,
                self.project,
                self.manager,
                clock=clock or now(),
                broker=self.broker,
                notifier=self.notifier,
            )
        except Exhausted as exc:
            emit(f'supervise: skipped, another cycle holds this target ({exc})')
            return None

    def cycles(self, clock: Callable[[], datetime] | None = None) -> Iterator[CycleResult]:
        """Yield one cycle at a time until the stop flag is set.

        Args:
            clock: a callable returning the instant to judge each cycle at. Defaults to the real
                one; a test passes its own rather than freezing the process's.

        Yields:
            Each cycle that ran, in order. A skipped cycle yields nothing.

        """
        tick = clock or now
        while not self.stop.is_set():
            result = self.once(tick())
            if result is not None:
                yield result
            self.stop.wait(self.interval())


@contextmanager
def _stopping(loop: Loop) -> Iterator[Loop]:
    """Set *loop*'s stop flag when the process is asked to terminate.

    SIGTERM and SIGINT are handled so a restart finishes the cycle in flight instead of landing
    mid-remedy.

    Args:
        loop: the loop to stop.

    Yields:
        The same loop.

    """
    previous: dict[int, signal.Handlers] = {}

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        loop.stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    try:
        yield loop
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def serve(
    loop: Loop,
    *,
    once: bool = False,
    on_cycle: Watcher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Run *loop* until stopped, reporting each cycle as it happens.

    Args:
        loop: the loop to run.
        once: run a single cycle and return, for a timer-driven deployment with no daemon.
        on_cycle: called with each cycle's result and the instant it ran, for a caller that keeps
            a journal. The loop itself has no opinion about where records go.
        clock: a callable returning the instant to judge each cycle at.

    Returns:
        The number of cycles that found something wrong, which is what an exit status should carry.

    """
    tick = clock or now
    degraded = 0
    with _stopping(loop) as running:
        for result in running.cycles(tick):
            at = tick()
            emit(f'supervise: {summary(result)}')
            if result.status != 'healthy':
                degraded += 1
            if on_cycle is not None:
                on_cycle(result, at)
            if once:
                running.stop.set()
    return degraded
