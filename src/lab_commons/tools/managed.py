"""THE MANAGED TOOL ROOT'S LAYOUT -- the one contract the install door writes and every probe reads.

Under the machine home's tool root (:func:`lab_commons.home.tools`, ``~/.motronics/tools`` or
``$MOTRONICS_HOME/tools``)::

    <tool>/<version>/                   one directory per release; the binary inside it at the release's path
    <tool>/<version>/<binary dir>/version.env
                                        the version file, where the tool's probe reads one
                                        (:data:`~lab_commons.compute.toolchecks.VERSION_FILES`)
    <tool>/installed.json               THE RECORD: the one installed, VERIFIED release of <tool>
    .staging/<tool>-<version>-<stamp>/  a fresh empty directory per install: the download and the installer log

A release directory is the install only once the record names it: the door writes the record after the probe
passes, so a half-written or failed install is never found. Every OS uses the same layout -- a Windows
installer's ``/DIR`` and a Linux source build's install prefix are both ``<tool>/<version>/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Final

from lab_commons import home
from lab_commons.compute.durable import durable_text, read_table

__all__ = ['NAME', 'RECORD', 'STAGING', 'Installed', 'install_dir', 'installed', 'record', 'record_path', 'staging']

#: The record of a tool's installed release, beside its release directories.
RECORD: Final = 'installed.json'

#: The staging part of the tool root: one fresh directory per install attempt.
STAGING: Final = '.staging'

#: A tool name or a version: one plain path component.
NAME: Final = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$')


def _component(kind: str, value: str) -> str:
    if not NAME.fullmatch(value):
        msg = f'{kind} {value!r} is not one plain path component ({NAME.pattern})'
        raise ValueError(msg)
    return value


def install_dir(tool: str, version: str) -> Path:
    """``<tool root>/<tool>/<version>``: where one release is installed."""
    return home.tools() / _component('tool', tool) / _component('version', version)


def record_path(tool: str) -> Path:
    """``<tool root>/<tool>/installed.json``."""
    return home.tools() / _component('tool', tool) / RECORD


def staging() -> Path:
    """``<tool root>/.staging``: the parent of every install attempt's fresh directory."""
    return home.tools() / STAGING


@dataclass(frozen=True)
class Installed:
    """One installed, verified release: *path* is its binary relative to its release directory."""

    tool: str
    version: str
    path: str
    source: str
    sha256: str | None
    at: str

    @property
    def root(self) -> Path:
        """The release directory."""
        return install_dir(self.tool, self.version)

    @property
    def binary(self) -> Path:
        """The binary the probe reads."""
        return self.root / self.path


def installed(tool: str) -> Installed | None:
    """*tool*'s record; ``None`` when nothing is installed through the door.

    Raises:
        ValueError: the record is not one this module writes -- reinstall through the door to rewrite it.

    """
    path = record_path(tool)
    row = read_table(path)
    if not row:
        return None
    names = {field.name for field in fields(Installed)}
    if set(row) != names or row.get('tool') != tool:
        msg = f'{path} is not an install record of {tool} (keys {sorted(row)}; expected {sorted(names)})'
        raise ValueError(msg)
    one = Installed(**row)
    relative = PurePosixPath(one.path)
    if relative.is_absolute() or '..' in relative.parts:
        msg = f'{path}: path {one.path!r} must lie inside the release directory'
        raise ValueError(msg)
    install_dir(tool, one.version)  # the version is one path component
    return one


def record(one: Installed) -> Path:
    """Write *one* as its tool's record (durably): from now on it is THE install the probe finds."""
    install_dir(one.tool, one.version)
    return durable_text(record_path(one.tool), json.dumps(asdict(one), indent=1) + '\n')
