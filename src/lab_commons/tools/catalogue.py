"""THE TOOL CATALOGUE -- ``tools.toml``, read and validated once: each tool's MODE, and a managed tool's releases.

TWO MODES. ``managed``: the door downloads a release, verifies its sha256 and installs it under the managed root
(:mod:`lab_commons.tools.install`). ``user-installed``: a commercial program licensed and installed by its own
vendor installer; the door cannot download or hash it, so it reads the install home this machine declares and
probes it (:mod:`lab_commons.tools.vendor`).

WHY A FAMILY TABLE, NOT ROWS BESIDE THE REQUIREMENT. A release's source, sha256, installer kind and binary path,
and a tool's mode, are facts about the TOOL, the same for every repo that needs it; which version a repo needs is a
fact about the REPO (its ``[tool.lab_commons.tools]``, :mod:`lab_commons.compute.presence`). Stated beside each
requirement, the one hash would be copied into every serving repo that declares the tool, and the per-machine door
would have to pick a repo to learn where FEMM comes from. So the catalogue lives once, with the door that reads it
and the probe that verifies what it installs; the requirement chooses among its rows (:func:`choose`).

THE FAMILY FLOOR. A tool whose older releases the family forbids states ``floor`` with its ``reason`` here (FEMM:
user ruling 2026-11-26, nothing older than 5.0.20261112). It is a fact about the TOOL, so it binds every repo: a
repo's own declaration composes with it (:func:`compose`, :func:`requirements`) and may only narrow it. A release
row below the floor is refused by name. The door installs the NEWEST release the composed requirement admits.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from lab_commons.compute.presence import Requirement, declared_versions, version_key
from lab_commons.compute.probe import OS_NAMES
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.tools.managed import NAME

__all__ = [
    'CATALOGUE',
    'KINDS',
    'MANAGED',
    'MODES',
    'USER_INSTALLED',
    'Release',
    'Tool',
    'choose',
    'compose',
    'load',
    'requirements',
]

#: The catalogue file, shipped beside this module.
CATALOGUE: Final = Path(__file__).with_name('tools.toml')

#: How a tool reaches a machine.
MANAGED: Final = 'managed'
USER_INSTALLED: Final = 'user-installed'
MODES: Final = (MANAGED, USER_INSTALLED)

#: How a managed release installs: an Inno Setup or MSI installer, a zip extracted in place, or a source build that
#: a deploy step makes (the door then verifies and records it).
KINDS: Final = ('inno', 'msi', 'zip', 'source-build')

_DOWNLOADED: Final = frozenset({'inno', 'msi', 'zip'})
_SHA256: Final = re.compile(r'^[0-9a-f]{64}$')
_TOOL_KEYS: Final = frozenset({'mode', 'vendor', 'releases', 'floor', 'reason'})
_KEYS: Final = frozenset({'kind', 'url', 'sha256', 'binary', 'builder', 'dir_property'})


@dataclass(frozen=True)
class Release:
    """One release of one managed tool for one OS, as the catalogue states it."""

    tool: str
    version: str
    system: str
    kind: str
    binary: str
    url: str | None = None
    sha256: str | None = None
    builder: str | None = None
    dir_property: str | None = None


@dataclass(frozen=True)
class Tool:
    """One catalogued tool: its *mode*, the ``[dev.vendors.<vendor>]`` holding a user-installed home, its releases.

    *floor*: the family's lowest allowed version with its reason, binding every repo; ``None`` when none is stated.
    """

    name: str
    mode: str
    vendor: str
    releases: Mapping[str, Mapping[str, Release]] = field(default_factory=dict)
    floor: Requirement | None = None


def _defect(row: Mapping[str, str]) -> str | None:
    """What is wrong with a row of string values, naming its key; ``None`` when it is a release."""
    kind, binary = row.get('kind'), row.get('binary', '')
    url, sha256, builder = row.get('url'), row.get('sha256'), row.get('builder')
    relative, downloaded = PurePosixPath(binary), kind in _DOWNLOADED
    rules = (
        (kind not in KINDS, f'kind = {kind!r}; the kinds are {list(KINDS)}'),
        (
            not binary or relative.is_absolute() or '..' in relative.parts or '\\' in binary,
            f'binary = {binary!r} must be a relative path inside the release directory',
        ),
        (downloaded and not url, f'a {kind} release needs its url'),
        (downloaded and not str(url).startswith('https://'), f'url = {url!r} must be https'),
        (
            downloaded and not _SHA256.fullmatch(sha256 or ''),
            f'sha256 = {sha256!r} must be the 64 lower-case hex digits the vendor publishes',
        ),
        (downloaded and builder is not None, f'a {kind} release is downloaded; builder belongs to source-build'),
        (
            kind == 'source-build' and (not builder or url is not None or sha256 is not None),
            'a source-build release names its builder (the deploy step) and no url or sha256',
        ),
        ('dir_property' in row and kind != 'msi', 'dir_property belongs to an msi release'),
    )
    return next((why for broken, why in rules if broken), None)


def _release(tool: str, version: str, system: str, row: object, where: str) -> Release:
    """One validated row; any defect is refused naming the row and the key."""
    if not isinstance(row, dict):
        why: str | None = 'must be a table'
    elif unknown := sorted(set(row) - _KEYS):
        why = f'unknown key(s) {unknown}; the keys are {sorted(_KEYS)}'
    elif not all(isinstance(value, str) for value in row.values()):
        why = 'every value is a string'
    else:
        why = _defect(row)
    if why is not None:
        msg = f'{where} [{tool}.releases."{version}".{system}]: {why}'
        raise ValueError(msg)
    return Release(tool, version, system, **row)


def _releases(tool: str, versions: object, where: str) -> dict[str, dict[str, Release]]:
    systems = sorted(set(OS_NAMES.values()))
    if not isinstance(versions, dict) or not versions:
        msg = f'{where} [{tool}]: a managed tool states its releases, [{tool}.releases."<version>".<os>]'
        raise ValueError(msg)
    table: dict[str, dict[str, Release]] = {}
    for version, rows in versions.items():
        if not NAME.fullmatch(version) or version_key(version) is None or not isinstance(rows, dict):
            msg = f'{where} [{tool}.releases."{version}"] must be a version (one plain path component) of OS rows'
            raise ValueError(msg)
        if unknown := sorted(set(rows) - set(systems)):
            msg = f'{where} [{tool}.releases."{version}"] names unknown OS {unknown}; the OSes are {systems}'
            raise ValueError(msg)
        table[version] = {system: _release(tool, version, system, row, where) for system, row in rows.items()}
    return table


def _tool(name: str, row: object, where: str) -> Tool:
    if not NAME.fullmatch(name) or not isinstance(row, dict):
        msg = f'{where} [{name}] must be a tool name (one plain path component) holding its mode'
        raise ValueError(msg)
    mode, vendor = row.get('mode'), row.get('vendor', name)
    unknown = sorted(set(row) - _TOOL_KEYS)
    unprobed = f'no lab-commons probe for {name}: a catalogued tool is verified by its check in '
    rules = (
        (bool(unknown), f'unknown key(s) {unknown}; the keys are {sorted(_TOOL_KEYS)}'),
        (mode not in MODES, f'mode = {mode!r}; the modes are {list(MODES)}'),
        (name not in CHECKS, unprobed + 'lab_commons.compute.toolchecks.CHECKS -- add the check first'),
        (mode == MANAGED and 'vendor' in row, 'vendor belongs to a user-installed tool'),
        (
            mode == USER_INSTALLED and 'releases' in row,
            'a user-installed tool has no releases: its vendor installer puts it where this machine declares',
        ),
        (not (isinstance(vendor, str) and NAME.fullmatch(vendor)), f'vendor = {vendor!r} must be one plain name'),
        (('floor' in row) != ('reason' in row), 'floor and reason come together: no floor without the reason for it'),
        (
            'floor' in row and not (isinstance(row['floor'], str) and version_key(row['floor']) is not None),
            f'floor = {row.get("floor")!r} must be a version',
        ),
        ('reason' in row and not (isinstance(row['reason'], str) and row['reason']), 'reason must say why'),
    )
    if (why := next((why for broken, why in rules if broken), None)) is not None:
        msg = f'{where} [{name}]: {why}'
        raise ValueError(msg)
    releases = _releases(name, row.get('releases'), where) if mode == MANAGED else {}
    floor = Requirement(row['floor'], row['reason']) if 'floor' in row else None
    if floor is not None and (below := sorted(v for v in releases if floor.refusal(v))):
        msg = f'{where} [{name}]: releases {below} are below the floor {floor.floor} ({floor.reason}): delete them'
        raise ValueError(msg)
    return Tool(name, mode, vendor, releases, floor)


def load(text: str, *, where: str = CATALOGUE.name) -> dict[str, Tool]:
    """``{tool: Tool}`` from the catalogue's TOML *text*.

    Raises:
        ValueError: a tool, mode, version or OS that is not one, or a malformed row -- each named with its key.

    """
    return {name: _tool(name, row, where) for name, row in tomllib.loads(text).items()}


def choose(
    table: Mapping[str, Tool], tool: str, system: str, requirement: Requirement | None, *, version: str | None = None
) -> Release:
    """The release of managed *tool* for *system*: *version* if given, else the newest *requirement* admits.

    Raises:
        LookupError: no such tool, not a managed one, no release for *system*, *version* not catalogued, or none
            that the requirement admits -- each naming what is catalogued and what to move.

    """
    if tool not in table:
        msg = f'no tool {tool!r} in {CATALOGUE.name}; catalogued: {sorted(table)}'
        raise LookupError(msg)
    if table[tool].mode != MANAGED:
        msg = f'{tool} is {table[tool].mode} in {CATALOGUE.name}: it has no release the door downloads'
        raise LookupError(msg)
    offered = {v: rows[system] for v, rows in table[tool].releases.items() if system in rows}
    if not offered:
        msg = f'{tool} has no release for {system} in {CATALOGUE.name}'
        raise LookupError(msg)
    bound = f'{requirement.text()} ({requirement.reason})' if requirement else 'none declared'
    if version is not None:
        if version not in offered:
            msg = f'{tool} {version} for {system} is not in {CATALOGUE.name}; catalogued: {sorted(offered)}'
            raise LookupError(msg)
        if requirement is not None and (why := requirement.refusal(version)):
            msg = f'{tool} {version} misses the requirement {bound}: {why}'
            raise LookupError(msg)
        return offered[version]
    admitted = [v for v in offered if requirement is None or requirement.refusal(v) is None]
    if not admitted:
        msg = (
            f'no {tool} release for {system} in {CATALOGUE.name} meets the requirement {bound}; catalogued: '
            f'{sorted(offered)} -- add the required release row (its url and measured sha256) to {CATALOGUE.name}'
        )
        raise LookupError(msg)
    return offered[max(admitted, key=lambda v: version_key(v) or ())]


def compose(tool: Tool, declared: Requirement | None) -> Requirement | None:
    """The requirement on *tool*: the repo's *declared* one inside the family floor; either alone when one is absent.

    A repo may only NARROW the family floor: a higher floor, an upper bound, or one release at or above it.

    Raises:
        ValueError: *declared* admits nothing at or above the family floor -- naming both and the floor's reason.

    """
    floor = tool.floor
    if floor is None or declared is None:
        return declared or floor
    if declared.exact:
        if why := floor.refusal(declared.floor):
            msg = f'{tool.name}: the declared one release {declared.floor} is {why}; declare {floor.floor} or newer'
            raise ValueError(msg)
        return declared
    if floor.refusal(declared.floor) is None:
        return declared
    composed = Requirement(floor.floor, floor.reason, declared.below, declared.incompatible)
    if declared.below is not None and composed.refusal(floor.floor):
        msg = (
            f'{tool.name}: the declared bound <{declared.below} ({declared.incompatible}) admits nothing at or above '
            f'the family floor {floor.floor} ({floor.reason})'
        )
        raise ValueError(msg)
    return composed


def requirements(root: Path | None, table: Mapping[str, Tool] | None = None) -> dict[str, Requirement]:
    """``{tool: requirement}`` for the repo at *root* (``None``: no repo): its declarations inside the family floors.

    The one reader of "which version is required" for every consumer: the door, ``tools check`` and the forge.

    Raises:
        ValueError: a malformed declaration (:func:`~lab_commons.compute.presence.declared_versions`) or one the
            family floor leaves empty (:func:`compose`).

    """
    tools = table if table is not None else load(CATALOGUE.read_text(encoding='utf-8'))
    declared = declared_versions(root) if root is not None else {}
    names = sorted(set(declared) | {name for name, tool in tools.items() if tool.floor is not None})
    composed = {name: compose(tools[name], declared.get(name)) if name in tools else declared[name] for name in names}
    return {name: one for name, one in composed.items() if one is not None}
