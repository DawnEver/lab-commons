"""The process manager for a host that runs systemd -- which is every host this family deploys to.

THE CEILING IS THE REASON THIS EXISTS. ``run_capped`` maps onto ``systemd-run --scope -p
MemoryMax=``, which is how a one-off command gets a cgroup of its own. That matters because the
alternative -- spawning a child from the supervisor -- puts it in the SUPERVISOR's cgroup, where the
limit it lands under is the wrong one and where killing it is indistinguishable from killing the
supervisor. A candidate build that forks is exactly the load that took this family's host down
once already.

The commands are built and then run through an injected runner, so what a manager WOULD execute is
assertable without a systemd on the machine running the test.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from lab_commons.supervise.process.base import Ran

__all__ = ['Runner', 'SystemdProcessManager', 'subprocess_runner']

#: Run an argv and report what it produced. Injected so a test can read the command rather than
#: execute it.
Runner = Callable[[list[str], int, 'str | None'], Ran]

#: How long a subprocess is given beyond the ceiling the command itself carries. The ``timeout``
#: inside the scope kills the command; this is the backstop for the scope machinery not coming up.
_GRACE: Final = 15


def subprocess_runner(argv: list[str], timeout: int, cwd: str | None) -> Ran:
    """Run *argv*, capturing both streams, and never raise for a failing command.

    Args:
        argv: the command and its arguments.
        timeout: seconds to allow.
        cwd: working directory, or None for the caller's.

    Returns:
        What the command produced, with -1 for a timeout.

    """
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Ran(code=-1, err=f'timed out after {timeout}')
    except OSError as exc:
        return Ran(code=-1, err=f'could not run {argv[0]}: {exc}')
    return Ran(code=done.returncode, out=done.stdout or '', err=done.stderr or '')


@dataclass(frozen=True)
class SystemdProcessManager:
    """Drive a service through ``systemctl``, and bound one-off work through ``systemd-run``.

    Attributes:
        systemctl: the control binary. Injectable for a host that spells it elsewhere.
        systemd_run: the transient-unit binary.
        runner: how a built command is executed.

    """

    systemctl: str = 'systemctl'
    systemd_run: str = 'systemd-run'
    runner: Runner = field(default=subprocess_runner)
    name: str = 'systemd'

    def is_active(self, unit: str) -> bool:
        """Report whether *unit* is running.

        Args:
            unit: the systemd unit name.

        Returns:
            True when ``systemctl is-active`` exits zero.

        """
        return self.runner([self.systemctl, 'is-active', unit], 30, None).ok

    def start(self, unit: str) -> Ran:
        """Start *unit*.

        Args:
            unit: the systemd unit name.

        Returns:
            What systemctl said.

        """
        return self.runner([self.systemctl, 'start', unit], 120, None)

    def stop(self, unit: str) -> Ran:
        """Stop *unit*.

        Args:
            unit: the systemd unit name.

        Returns:
            What systemctl said.

        """
        return self.runner([self.systemctl, 'stop', unit], 120, None)

    def restart(self, unit: str) -> Ran:
        """Restart *unit*.

        Args:
            unit: the systemd unit name.

        Returns:
            What systemctl said.

        """
        return self.runner([self.systemctl, 'restart', unit], 180, None)

    def start_capped(
        self,
        argv: list[str],
        *,
        memory_max: str,
        name: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Ran:
        """Start a transient unit carrying a memory ceiling.

        ``--collect`` so a unit that exits is garbage-collected rather than left as a failed unit
        for the next ``systemctl list-units`` to explain.

        Args:
            argv: the command and its arguments.
            memory_max: the ceiling, e.g. ``600M``.
            name: the unit's name.
            cwd: working directory, or None for the caller's.
            env: extra environment for the process.

        Returns:
            What systemd-run said.

        """
        command = [
            self.systemd_run,
            f'--unit={name}',
            '--collect',
            '--quiet',
            f'--property=MemoryMax={memory_max}',
        ]
        if cwd:
            command.append(f'--working-directory={cwd}')
        for key, value in (env or {}).items():
            command.append(f'--setenv={key}={value}')
        command.extend(['--', *argv])
        return self.runner(command, 60, None)

    def stop_unit(self, name: str) -> Ran:
        """Stop a transient unit by name.

        Args:
            name: the unit's name.

        Returns:
            What systemctl said. A unit that is already gone is stopped.

        """
        if not self.is_active(name):
            return Ran(code=0, out='already gone')
        return self.runner([self.systemctl, 'stop', name], 120, None)

    def run_capped(self, argv: list[str], *, memory_max: str, timeout: int, cwd: str | None = None) -> Ran:
        """Run *argv* in a transient scope carrying a memory ceiling.

        ``timeout`` is applied INSIDE the scope, so the command is killed within its own cgroup:
        a ``timeout`` applied outside would leave the scope's processes running after the wrapper
        had gone.

        Args:
            argv: the command and its arguments.
            memory_max: the ceiling, e.g. ``600M``.
            timeout: seconds to allow the command.
            cwd: working directory, or None for the caller's.

        Returns:
            What the command produced.

        """
        command = [
            self.systemd_run,
            '--scope',
            '--quiet',
            f'--property=MemoryMax={memory_max}',
            '--',
            'timeout',
            str(timeout),
            *argv,
        ]
        return self.runner(command, timeout + _GRACE, cwd)
