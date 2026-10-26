"""THE HOST'S OS as a resource name: ``windows``, ``linux`` or ``macos``.

A box counts its OS as many as its free ``cpu`` (:class:`lab_commons.box.providers.BoxOS`), a Slurm account its
``linux`` (:class:`lab_commons.hpc.provider.SlurmAccount`): a part needing an OS is admitted only where it is.
What a host or account can take is measured by its providers, never here.
"""

from __future__ import annotations

import sys
from typing import Final

__all__ = ['OS_NAMES', 'os_name']

#: ``sys.platform`` -> the resource name of the OS.
OS_NAMES: Final = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}


def os_name(platform: str = sys.platform) -> str:
    """The OS resource of *platform*; an OS the family does not serve is refused by name."""
    if platform not in OS_NAMES:
        msg = f'no OS resource for sys.platform {platform!r}; known: {sorted(OS_NAMES)}'
        raise ValueError(msg)
    return OS_NAMES[platform]
