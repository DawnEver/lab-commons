"""Every ``lab_commons.hpc`` ssh to a login host SHARES a connection and is RATE-LIMITED, machine-wide.

User ruling 2026-11-11: every agent may use the key-only doors, but the 2026-11-06 ban came from agents opening
dozens of ssh connections in poll loops until the login nodes refused ssh. So the one ssh runner
(:func:`lab_commons.hpc.cluster.ssh_runner`) goes through a :class:`Gate` per host:

- SHARING, per platform. POSIX: an OpenSSH ControlMaster (``ControlMaster=auto``, a ``ControlPath`` under the
  machine home's ``state/hpc-ssh`` keyed by a hash of ``user@host``, ``ControlPersist`` :data:`PERSIST`), so
  every agent and session on the machine rides ONE connection per host. Windows: OpenSSH for Windows has no
  multiplexing, so a machine-wide per-host file lock serializes the calls -- at most one live ssh per host.
- RATE, machine-wide, per host (a state file under the same lock): a door invocation (one runner, however many
  commands it sends) takes one slot; slots are at least ``min_interval_s`` apart and at most ``max_per_window``
  per ``window_s``. Over the limit it REFUSES (:class:`RateLimited`) naming the wait and the server-side
  pollers -- it never sleeps. Defaults :data:`LIMITS`; the machine file's ``[hpc.ssh]`` overrides them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.config import machine
from lab_commons.home import part

__all__ = ['LIMITS', 'PERSIST', 'Gate', 'Limits', 'RateLimited', 'limits_of']

#: How long an idle shared master stays up after its last command.
PERSIST: Final = '10m'


@dataclass(frozen=True)
class Limits:
    """New ssh sessions per host: at least *min_interval_s* apart, at most *max_per_window* per *window_s*."""

    min_interval_s: float = 5.0
    max_per_window: int = 30
    window_s: float = 600.0

    def __post_init__(self) -> None:
        """A limit that admits nothing, or everything, is refused."""
        if self.min_interval_s < 0 or self.max_per_window < 1 or self.window_s <= 0:
            msg = f'an unusable [hpc.ssh] limit: {self}'
            raise ValueError(msg)


LIMITS: Final = Limits()


def limits_of(table: dict[str, Any] | None = None) -> Limits:
    """The machine file's ``[hpc.ssh]`` over :data:`LIMITS`; an unknown key is refused by name."""
    raw = (machine().get('hpc', {}) if table is None else table).get('ssh', {})
    if unknown := set(raw) - set(Limits.__dataclass_fields__):
        msg = f'[hpc.ssh] has unknown keys {sorted(unknown)}; known: {sorted(Limits.__dataclass_fields__)}'
        raise ValueError(msg)
    return Limits(**raw)


class RateLimited(RuntimeError):
    """A new ssh session to a host would exceed its machine-wide rate; nothing ran."""


_FAR: Final = 2**31 - 2


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive machine-wide lock on *path*, blocking until it is free."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt  # noqa: PLC0415 -- the platform's own lock

            handle.seek(_FAR)
            while True:  # LK_LOCK gives up after ~10 s; a held ssh call may take an hour
                with contextlib.suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
            try:
                yield
            finally:
                handle.seek(_FAR)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 -- the platform's own lock

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class Gate:
    """One host's sharing and rate state; one per runner, so its first command takes the session's slot."""

    host: str
    root: Path = field(default_factory=lambda: part('state') / 'hpc-ssh')
    limits: Limits | None = None
    platform: str = sys.platform
    clock: Callable[[], float] = time.time
    _admitted: bool = False

    @property
    def key(self) -> str:
        """The host's file stem: a hash, so no fleet name lands in a path."""
        return hashlib.sha256(self.host.encode()).hexdigest()[:16]

    @property
    def multiplexed(self) -> bool:
        """POSIX shares one ControlMaster; Windows OpenSSH cannot, so it serializes on a lock."""
        return self.platform != 'win32'

    def options(self) -> list[str]:
        """The ssh ``-o`` options that share the connection (none where the platform cannot)."""
        if not self.multiplexed:
            return []
        sock = self.root / f'{self.key}.sock'
        return ['-o', 'ControlMaster=auto', '-o', f'ControlPath={sock.as_posix()}', '-o', f'ControlPersist={PERSIST}']

    def _admit(self) -> None:
        limits = self.limits or limits_of()
        state = self.root / f'{self.key}.json'
        with _locked(self.root / f'{self.key}.rate.lock'):
            now = self.clock()
            stamps = json.loads(state.read_text(encoding='utf-8')) if state.is_file() else []
            stamps = [s for s in stamps if now - s < limits.window_s]
            waits = []
            if stamps and now - stamps[-1] < limits.min_interval_s:
                waits.append(limits.min_interval_s - (now - stamps[-1]))
            if len(stamps) >= limits.max_per_window:
                waits.append(limits.window_s - (now - stamps[0]))
            if waits:
                msg = (
                    f'hpc ssh rate limit: the next session to this host is allowed in {max(waits):.0f}s '
                    f'({len(stamps)}/{limits.max_per_window} in {limits.window_s:g}s, at least '
                    f'{limits.min_interval_s:g}s apart; [hpc.ssh] in the machine file). Do not poll in a loop: '
                    'read where a run is with `python -m dev_lab.dev.forge queue`, or let ONE '
                    '`python -m lab_commons.hpc gather -c <job> --wait` poll (one session, polls >= 60 s apart).'
                )
                raise RateLimited(msg)
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps([*stamps, now]), encoding='utf-8')
        self._admitted = True

    @contextlib.contextmanager
    def call(self) -> Iterator[None]:
        """Hold for one ssh command: the session's slot on its first use, the host lock where it cannot share."""
        if not self._admitted:
            self._admit()
        if self.multiplexed:
            yield
            return
        with _locked(self.root / f'{self.key}.lock'):
            yield
