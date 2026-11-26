"""A USER-INSTALLED TOOL: licensed and installed by its vendor's own installer; the door RECORDS and PROBES it.

JMAG, Ansys, MATLAB and the like cannot be downloaded or hashed by the family: a person installs them with the
vendor installer and a licence. The door's job is to know WHERE this machine has it and to PROBE that install back
through the same lab-commons probe (:data:`~lab_commons.compute.toolchecks.CHECKS`, its ``within`` reader rooted at
the declared home), at the version the serving repo requires.

ONE STORE FOR THE HOME. A machine's vendor install homes already live in the config source's host file,
``hosts/<host>.toml`` ``[dev.vendors.<vendor>] home`` (dev-lab's ``devhost``; ``devhost detect`` proposes them,
and the drift notice names an unfilled one); the machine file names that host and clone in ``[devhost]``. The
door READS it there and never keeps a second copy. It never writes it either -- a host file changes through its
pull request -- so a home the door verifies but the host file does not name is refused with the exact line to
record.

Each refusal names its door: ``install <tool> <version> with the vendor installer, then record it``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from lab_commons import config
from lab_commons.compute.presence import Requirement, probe
from lab_commons.compute.toolchecks import CHECKS, Check
from lab_commons.home import canonical
from lab_commons.tools.catalogue import Tool
from lab_commons.tools.install import ToolReport

__all__ = ['HOST_TABLE', 'UNFILLED', 'Declared', 'declared_home', 'record']

#: The machine-file table naming this machine's host and its config-source clone (dev-lab ``devhost`` renders it).
HOST_TABLE: Final = 'devhost'

#: dev-lab's placeholder for a home a machine has yet to fill (``dev_lab.dev.devschema.UNFILLED``), restated:
#: lab-commons never imports dev-lab.
UNFILLED: Final = 'FILL-ON-THIS-MACHINE'


@dataclass(frozen=True)
class Declared:
    """The home this machine declares for one vendor (``None`` when it declares none), and where it is read."""

    home: Path | None
    where: str


def declared_home(vendor: str, *, machine: Mapping[str, Any] | None = None) -> Declared:
    """``[dev.vendors.<vendor>] home`` of this machine's host file, located through the machine file's ``[devhost]``."""
    own = (config.machine() if machine is None else machine).get(HOST_TABLE, {})
    host, source = own.get('host'), own.get('source')
    if not host or not source:
        why = f'{config.config_path()} names no [{HOST_TABLE}] host and source: it is not rendered from a host file'
        return Declared(None, why)
    file = Path(source) / 'hosts' / f'{host}.toml'
    if not file.is_file():
        return Declared(None, f'{file} (missing: update the config-source clone)')
    vendors = tomllib.loads(file.read_text(encoding='utf-8')).get('dev', {}).get('vendors', {})
    home = vendors.get(vendor, {}).get('home')
    where = f'{file} [dev.vendors.{vendor}]'
    if not isinstance(home, str) or not home or home == UNFILLED:
        return Declared(None, where)
    return Declared(Path(home), where)


def _wanted(tool: str, requirement: Requirement | None) -> str:
    if requirement is None:
        return f'install {tool}'
    release = requirement.floor if requirement.exact else f'{requirement.floor} or newer'
    return f'install {tool} {release}'


def record(
    tool: Tool,
    requirement: Requirement | None,
    *,
    home: Path | None = None,
    checks: Mapping[str, Check] = CHECKS,
    now: datetime,
    machine: Mapping[str, Any] | None = None,
) -> ToolReport:
    """Probe user-installed *tool* at *home*, else at its declared home; ``recorded`` only when both agree.

    The outcome is ``refused`` -- naming the door -- when no home is declared or given, the probe finds no binary
    under it, the version misses *requirement*, or a verified *home* is not the one the host file records.
    """
    declared = declared_home(tool.vendor, machine=machine)
    door = f'{_wanted(tool.name, requirement)} with the vendor installer, then record it'
    line = f'[dev.vendors.{tool.vendor}] home = "<install dir>"'
    target = home or declared.home
    if target is None:
        msg = (
            f'{tool.name} is user-installed and this machine declares no install home ({declared.where}): {door} -- '
            f'{line} in the host file through its pull request (`python -m dev_lab.dev.devhost detect` proposes it), '
            'or pass --home <install dir> to verify one first'
        )
        return ToolReport('refused', msg)
    check = checks[tool.name]
    found = check.within(target) if check.within is not None else None
    if found is None:
        return ToolReport('refused', f'no {tool.name} under {target}: {door}')
    reading = probe(tool.name, replace(check, find=lambda: found), requirement, now=now)
    if not reading.present:
        evidence = '; '.join(f'{one.source}: {one.value}' for one in reading.evidence)
        return ToolReport('refused', f'{tool.name} at {found[1]} is refused by its probe ({evidence}): {door}')
    version = reading.version or 'its version'
    if declared.home is None or canonical(declared.home) != canonical(target):
        msg = (
            f'{tool.name} {version} at {target} verifies, but this machine records '
            f'{declared.home or "no home"} ({declared.where}): record it -- set [dev.vendors.{tool.vendor}] '
            f'home = "{target.as_posix()}" in the host file through its pull request, then render the machine file'
        )
        return ToolReport('refused', msg)
    return ToolReport('recorded', f'{tool.name} {version} at {found[1]} verifies; recorded in {declared.where} home')
