"""THE HOST CONTEXT: lab-commons' one ENV-IS-READ-ONCE entry, where the process environment is read.

Family policy 2026-11-25 (v1-migration D67/D68): a fact an external system offers only as an environment variable
(class B) is read at ONE entry and turned into a typed record at once; host and scheduler facts are lab-commons',
and it is this module (``[tool.dev_lab.env] entry``). Nothing read here may change a numeric result. One door,
:func:`context`, the typed :class:`Host` record of the facts lab-commons reads by NAME; a caller passes the record
down (:func:`lab_commons.home.home` takes one) instead of reading the environment again.

THE MACHINE-HOME OVERRIDE (:data:`HOME_ENV`) is class B, not configuration: it says where the per-machine
configuration itself lives, so no configuration can hold it, and a test relocates the home for itself AND for every
process it spawns, which only the environment reaches (the family's test fixtures set it; no host and no CI
workflow does). A value that changes a result or a behavior (class A) is configuration, and a test switch (class C)
a pytest option; neither is added here, because this module would then be the laundering door the rule closes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ['HEAVY_APPROVED_ENV', 'HOME_ENV', 'XDG_CONFIG_ENV', 'Host', 'context']

#: Relocates the machine home (``~/.motronics``): a test's own, or a copy the operator points a run at.
HOME_ENV: Final = 'MOTRONICS_HOME'
#: The XDG base directory for user configuration, where git's ``store`` helper keeps its second file.
XDG_CONFIG_ENV: Final = 'XDG_CONFIG_HOME'
#: The user's own words approving ONE heavy run on a workstation (:mod:`lab_commons.box.heavy`): a consent, typed by
#: the user and passed by whoever starts the run, never a setting a file holds.
HEAVY_APPROVED_ENV: Final = 'LAB_COMMONS_HEAVY_APPROVED'


@dataclass(frozen=True, slots=True)
class Host:
    """The host facts lab-commons reads by name; ``''`` where the environment does not say."""

    machine_home: str = ''
    xdg_config_home: str = ''
    local_appdata: str = ''
    user_profile: str = ''
    user_home: str = ''
    heavy_approved: str = ''


def context(env: Mapping[str, str] | None = None) -> Host:
    """The :class:`Host` record of *env* (default: this process's environment, read now)."""
    env = os.environ if env is None else env
    return Host(
        machine_home=env.get(HOME_ENV, ''),
        xdg_config_home=env.get(XDG_CONFIG_ENV, ''),
        local_appdata=env.get('LOCALAPPDATA', ''),
        user_profile=env.get('USERPROFILE', ''),
        user_home=env.get('HOME', ''),
        heavy_approved=env.get(HEAVY_APPROVED_ENV, ''),
    )
