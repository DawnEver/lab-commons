"""NO-LOCAL-HEAVY: a heavy compute entry never starts on a declared workstation (user ruling 2026-11-26).

A repo's core optimization programs (a campaign driver, a connection search, a product enumeration, a rank or merge
pipeline) saturate every core and the memory of the box they start on; started on a workstation they froze it, and the
user had to kill one. They run only where heavy compute is DECLARED: the forge, an HPC executor, or a compute host.

THE HOST FACT IS THE MACHINE FILE'S, read here once (:func:`refusal`). A host whose ``[capabilities]`` declares
:data:`WORKSTATION` and not :data:`CAPABILITY` refuses; every other host proceeds -- a compute host declares
``[capabilities.heavy_compute]``, and a host with no machine file is no DECLARED workstation (the cluster's per-sha
tree, a test's planted home). The refusal names its cause and its door (:data:`DOOR`).

THE OVERRIDE IS THE USER'S OWN WORDS, never an agent's: :data:`lab_commons.hostenv.HEAVY_APPROVED_ENV` holding the
words the user typed approving this one run, the family's approval-words pattern (a version tag's
``DEV_LAB_TAG_APPROVED``). It is read through the host context, never from the environment here.

Three callers, one judgment: an entry's ``main`` calls :func:`require` before any work; a command outside Python asks
``python -m lab_commons.box.heavy check <entry>`` (exit 2 refuses); dev-lab's agent guard row and its pytest
plugin read :func:`refusal`.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

from lab_commons import hostenv
from lab_commons.box.machine import provided
from lab_commons.config import section
from lab_commons.log import emit

__all__ = ['CAPABILITY', 'DOOR', 'EXIT_REFUSED', 'RULE', 'WORKSTATION', 'HeavyRefused', 'main', 'refusal', 'require']

#: The rule's handle, cited by the agent guard row and the docs.
RULE: Final = 'NO-LOCAL-HEAVY'
#: The ``[capabilities.<name>]`` a host declares to run heavy compute -- and the need marker of a heavy test.
CAPABILITY: Final = 'heavy_compute'
#: The ``[capabilities.<name>]`` a workstation deploy writes (dev-lab's ``workstation deploy``).
WORKSTATION: Final = 'workstation'
#: The exit code of a refusal: a usage-class code, never a child's.
EXIT_REFUSED: Final = 2
#: Where a heavy entry runs instead.
DOOR: Final = (
    'Door: run it where heavy compute is declared -- request it on the forge from the main session '
    '(`python -m dev_lab.dev.forge request ...`), submit it to an HPC executor '
    '(`python -m lab_commons.hpc submit -c <job.toml>`), or on a compute host whose machine file declares '
    f'[capabilities.{CAPABILITY}].'
)


class HeavyRefused(SystemExit):
    """A heavy entry refused on this host; its message is the refusal, its code :data:`EXIT_REFUSED`."""

    def __init__(self, message: str) -> None:
        """Carry *message* for the caller and exit with :data:`EXIT_REFUSED`."""
        super().__init__(EXIT_REFUSED)
        self.message = message

    def __str__(self) -> str:
        """The refusal text, not the exit code."""
        return self.message


def refusal(
    entry: str,
    *,
    capabilities: Mapping[str, Any] | None = None,
    host: hostenv.Host | None = None,
    hostname: str | None = None,
) -> str | None:
    """Why *entry* may not start on this host, or ``None`` when it may.

    *capabilities* defaults to the machine file's ``[capabilities]`` (validated, :func:`provided`), *host* to the
    process's host context, *hostname* to this computer's name; each is injectable so a test plants both answers.
    """
    caps = provided(section('capabilities')) if capabilities is None else capabilities
    if WORKSTATION not in caps or CAPABILITY in caps:
        return None
    if (hostenv.context() if host is None else host).heavy_approved.strip():
        return None
    name = socket.gethostname() if hostname is None else hostname
    return (
        f'{RULE}: {entry} is a heavy compute entry, and this host ({name}) is a declared workstation without '
        f'[capabilities.{CAPABILITY}] -- started here it freezes the whole box (user ruling 2026-11-26). {DOOR} '
        f'Only the user may override it for one run: {hostenv.HEAVY_APPROVED_ENV}="<the user`s own words>".'
    )


def require(
    entry: str,
    *,
    capabilities: Mapping[str, Any] | None = None,
    host: hostenv.Host | None = None,
    hostname: str | None = None,
) -> None:
    """Return when *entry* may start here; otherwise print the refusal and raise :class:`HeavyRefused`.

    Called FIRST in a heavy entry's ``main``, before it parses work, spawns or computes anything; the facts are
    :func:`refusal`'s.
    """
    if (why := refusal(entry, capabilities=capabilities, host=host, hostname=hostname)) is not None:
        emit(why, err=True, flush=True)
        raise HeavyRefused(why)


def main(argv: Sequence[str] | None = None) -> int:
    """``check <entry>``: exit 0 when *entry* may start here, else print the refusal and exit 2."""
    parser = argparse.ArgumentParser(prog='python -m lab_commons.box.heavy', description=main.__doc__)
    verbs = parser.add_subparsers(dest='verb', required=True)
    verbs.add_parser('check').add_argument('entry')
    args = parser.parse_args(argv)
    try:
        require(args.entry)
    except HeavyRefused:
        return EXIT_REFUSED
    return 0


if __name__ == '__main__':
    sys.exit(main())
