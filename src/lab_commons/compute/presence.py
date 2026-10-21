"""IS A VENDOR TOOL REALLY THERE -- several sources, one verdict, with its evidence.

A tool is PRESENT on a host only when all four agree:

1. its executable is FOUND -- a declared path (an environment variable the tool's own docs name), a
   standard install directory, the registry or ``PATH``;
2. its VERSION reads (``--version``, the executable's file version, its install path, its package
   metadata) and meets the FLOOR the serving repo declares ONCE for the family, in its ``pyproject.toml``
   ``[tool.lab_commons.tools]`` (``<tool> = {floor = "<version>", reason = "<why>"}``; ``below`` only with
   its stated ``incompatible``). A declaration is a floor, never a pin (user ruling 2026-10-17: no pin
   unless a reason requires it -- record, don't pin): a tool with no declaration is present at any version
   that reads, and the version read is RECORDED in every run record (:func:`measured`);
3. a SMOKE run passes -- the tool does one trivial thing end to end (:data:`~lab_commons.compute.toolchecks.CHECKS`);
4. no real unit on that tool has failed on this host since that smoke (:func:`demote`).

Each step leaves :class:`Evidence` (source, value, time), and the host card shows it
(:mod:`lab_commons.dev.forgehosts`): the card is the probe's reading, never a declaration. Seat counts
are the one thing a person declares (:mod:`lab_commons.box.machine`), because no probe can read a licence.

A smoke costs seconds to minutes (MATLAB starting), so a reading is CACHED for :data:`TTL` in
``tool-presence.json`` beside the machine file, with the requirement it was taken against. :func:`refresh`
re-probes what is due -- older than the TTL, demoted since its smoke, or taken against another requirement
-- and ``forge serve`` calls it once per tick; :func:`cached` only reads, and a reading past its TTL is
absent, so a host nobody refreshes stops claiming. A reading that is MERELY stale -- every clause passed,
only its age failed -- is not evidence of absence, so a unit about to run calls :func:`renew`, which
re-probes it in place (one probe per process, however many station threads ask) and admits on a pass.
All readers of the OS are module functions behind
:class:`~lab_commons.compute.toolchecks.Check` (:mod:`lab_commons.compute.toolchecks`), so a test passes fakes.
"""

from __future__ import annotations

import json
import re
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from lab_commons.compute.toolchecks import CHECKS, Check
from lab_commons.config import config_path

__all__ = [
    'TTL',
    'Evidence',
    'Presence',
    'Requirement',
    'cached',
    'declared_versions',
    'demote',
    'measured',
    'probe',
    'refresh',
    'renew',
    'stale',
]

#: How long one probe's reading holds before :func:`refresh` runs it again and :func:`cached` reads it absent.
TTL: Final = timedelta(hours=6)

#: What a declaration looks like, quoted by the refusal of any other shape.
SHAPE: Final = 'femm = {floor = "5.0", reason = "<why no lower version serves>"}'


@dataclass(frozen=True)
class Evidence:
    """One reading: which source said it, what it said, and when (ISO 8601, UTC)."""

    source: str
    value: str
    time: str


@dataclass(frozen=True)
class Presence:
    """One tool on this host: the verdict and every reading it rests on, in probe order."""

    tool: str
    present: bool
    evidence: tuple[Evidence, ...] = ()

    def row(self) -> dict[str, Any]:
        """The card's form: ``{present, evidence: [{source, value, time}, ...]}``."""
        return {'present': self.present, 'evidence': [asdict(one) for one in self.evidence]}

    @classmethod
    def of_row(cls, tool: str, row: Mapping[str, Any]) -> Presence:
        """The inverse of :meth:`row`."""
        return cls(tool, bool(row['present']), tuple(Evidence(**one) for one in row.get('evidence', ())))

    @property
    def version(self) -> str | None:
        """The version the probe READ, when the tool is present -- the one source a record states."""
        read = [e.value for e in self.evidence if e.source.startswith('version: ')]
        return read[-1] if self.present and read else None


def measured(presence: Mapping[str, Presence]) -> dict[str, str]:
    """``{tool: the version read}`` over every present tool: what a run record states it ran against."""
    return {tool: one.version for tool, one in sorted(presence.items()) if one.version is not None}


# --- the requirement -----------------------------------------------------------------------------------


def _key(text: str) -> tuple[tuple[int, int | str], ...] | None:
    """A printed version's ordering key, over its first token carrying a digit (``FEMM 5.0 (b7)`` -> ``5.0``).

    Digit runs compare as numbers and letter runs as text, so ``25.2.01zp`` >= ``25.2`` and ``R2025b`` >
    ``R2024b``; no token with a digit is ``None`` (unordered).
    """
    token = next((t for t in re.split(r'[^\w.]+', text) if re.search(r'\d', t)), None)
    if token is None:
        return None
    runs = re.findall(r'\d+|[a-z]+', token.lower().removeprefix('v'))
    return tuple((1, int(run)) if run.isdigit() else (0, run) for run in runs)


@dataclass(frozen=True)
class Requirement:
    """A declared FLOOR with its reason; an upper bound (*below*) only with its stated *incompatible*."""

    floor: str
    reason: str
    below: str | None = None
    incompatible: str = ''

    def refusal(self, read: str) -> str | None:
        """Why *read* misses this requirement, naming the bound and its reason; ``None`` when it meets it."""
        version, floor = _key(read), _key(self.floor)
        if version is None or floor is None:
            return f'unordered: {read!r} against the floor {self.floor} ({self.reason})'
        if version < floor:
            return f'below the floor {self.floor} ({self.reason})'
        below = _key(self.below) if self.below is not None else None
        if below is not None and not version < below:
            return f'not below {self.below} ({self.incompatible})'
        return None

    def text(self) -> str:
        """One line naming the bounds: what a cached reading was taken against."""
        return f'>={self.floor}' + (f',<{self.below}' if self.below is not None else '')


def _well_formed(row: object) -> bool:
    if not isinstance(row, dict) or not set(row) <= {'floor', 'reason', 'below', 'incompatible'}:
        return False
    if not all(isinstance(value, str) for value in row.values()):
        return False
    return (
        bool(row.get('floor')) and bool(row.get('reason')) and bool(row.get('below')) == bool(row.get('incompatible'))
    )


def declared_versions(root: Path) -> dict[str, Requirement]:
    """The serving repo's ``[tool.lab_commons.tools]``: ``{tool: its floor}``.

    Raises:
        ValueError: a bare version string (an exact pin), a floor without its reason, or an upper bound
            without its stated incompatibility.

    """
    manifest = root / 'pyproject.toml'
    if not manifest.is_file():
        return {}
    table = tomllib.loads(manifest.read_text(encoding='utf-8')).get('tool', {}).get('lab_commons', {}).get('tools', {})
    if bad := sorted(name for name, row in table.items() if not _well_formed(row)):
        msg = (
            f'{manifest} [tool.lab_commons.tools]: {bad} must each be a floor with its reason, {SHAPE}, and '
            '`below` only with `incompatible`; a bare version string would pin, and a tool needing no floor '
            'is not declared'
        )
        raise ValueError(msg)
    return {
        name: Requirement(row['floor'], row['reason'], row.get('below'), row.get('incompatible', ''))
        for name, row in table.items()
    }


# --- the verdict ---------------------------------------------------------------------------------------


def probe(tool: str, check: Check, declared: Requirement | None, *, now: datetime) -> Presence:
    """Steps 1-3 for one tool, stopping at the first that fails; every reading becomes evidence."""
    at = now.isoformat()
    found = check.find()
    if found is None:
        return Presence(tool, present=False, evidence=(Evidence('find', 'not found', at),))
    source, path = found
    seen = [Evidence(f'find: {source}', str(path), at)]
    version = check.version(path)
    if version is None:
        return Presence(tool, present=False, evidence=(*seen, Evidence('version', 'unreadable', at)))
    seen.append(Evidence(f'version: {version[0]}', version[1], at))
    if declared is not None and (why := declared.refusal(version[1])):
        return Presence(tool, present=False, evidence=(*seen, Evidence('version', why, at)))
    passed, detail = check.smoke(path)
    return Presence(
        tool, present=passed, evidence=(*seen, Evidence('smoke', f'{"passed" if passed else "FAILED"}: {detail}', at))
    )


def _state(path: Path | None) -> Path:
    return path or config_path().parent / 'tool-presence.json'


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=1), encoding='utf-8')
    temp.replace(path)


def _required(requirement: Requirement | None) -> str:
    return requirement.text() if requirement is not None else 'any'


def _current(entry: Mapping[str, Any], required: str, now: datetime, ttl: timedelta) -> bool:
    """A reading still holds: inside its TTL, taken against today's requirement, and no unit failed since.

    An entry with no ``required`` was written when a declaration was an exact pin; upgraded on read it is
    due, so the next refresh re-probes it against its floor (FORMATS-UPGRADE-ON-READ).
    """
    probed = datetime.fromisoformat(entry['probed'])
    failed = entry.get('failed')
    fresh = now - probed < ttl and entry.get('required') == required
    return fresh and (failed is None or datetime.fromisoformat(failed) < probed)


def _verdict(tool: str, entry: Mapping[str, Any], now: datetime, ttl: timedelta) -> Presence:
    one = Presence.of_row(tool, entry)
    if entry.get('failed') and datetime.fromisoformat(entry['failed']) >= datetime.fromisoformat(entry['probed']):
        return Presence(
            tool, present=False, evidence=(*one.evidence, Evidence('failure', entry.get('why', ''), entry['failed']))
        )
    if now - datetime.fromisoformat(entry['probed']) >= ttl:
        return Presence(
            tool, present=False, evidence=(*one.evidence, Evidence('ttl', f'older than {ttl}', now.isoformat()))
        )
    return one


def cached(
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> dict[str, Presence]:
    """Every checked tool's last reading, read only: never probed is absent, and so is stale or demoted."""
    now = now or datetime.now(UTC)
    stored = _load(_state(state))
    return {
        tool: _verdict(tool, stored[tool], now, ttl)
        if tool in stored
        else Presence(tool, present=False, evidence=(Evidence('probe', 'never run on this host', now.isoformat()),))
        for tool in checks
    }


def refresh(
    versions: Mapping[str, Requirement],
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> dict[str, Presence]:
    """Re-probe every tool whose reading is due against *versions*; then :func:`cached`.

    Due: missing, stale, demoted, or taken against another requirement than *versions* states now.
    """
    now = now or datetime.now(UTC)
    path = _state(state)
    stored = _load(path)
    required = {tool: _required(versions.get(tool)) for tool in checks}
    due = [tool for tool in checks if tool not in stored or not _current(stored[tool], required[tool], now, ttl)]
    for tool in due:
        row = probe(tool, checks[tool], versions.get(tool), now=now).row()
        stored[tool] = {**row, 'probed': now.isoformat(), 'required': required[tool]}
    if due:
        _save(path, stored)
    return cached(checks=checks, state=path, now=now, ttl=ttl)


#: One re-probe at a time in this process: a caller that waited re-reads the state the prober wrote.
_RENEWING: Final = threading.Lock()


def _requirement(required: str) -> Requirement | None:
    """The inverse of :meth:`Requirement.text` (``any``: none); a stored floor carries no reason, so it says so."""
    if not required.startswith('>='):
        return None
    floor, _, below = required.removeprefix('>=').partition(',<')
    return Requirement(floor, 'as cached', below or None, 'as cached' if below else '')


def stale(one: Presence) -> bool:
    """Absent ONLY because the reading aged past its TTL: every clause it records passed."""
    return not one.present and bool(one.evidence) and one.evidence[-1].source == 'ttl'


def renew(
    tool: str,
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> Presence:
    """*tool*'s reading, re-probed in place when it is merely stale (:func:`stale`) against its stored floor.

    A failed, demoted or never-taken reading is returned as it is: retaking those is :func:`refresh`'s.
    """
    path = _state(state)
    with _RENEWING:
        at = now or datetime.now(UTC)
        one = cached(checks={tool: checks[tool]}, state=path, now=at, ttl=ttl)[tool]
        stored = _load(path)
        if not stale(one) or not stored[tool].get('present'):
            return one
        required = stored[tool].get('required', 'any')
        row = probe(tool, checks[tool], _requirement(required), now=at).row()
        stored[tool] = {**row, 'probed': at.isoformat(), 'required': required}
        _save(path, stored)
        return cached(checks={tool: checks[tool]}, state=path, now=at, ttl=ttl)[tool]


def demote(tools: Sequence[str], why: str, *, state: Path | None = None, now: datetime | None = None) -> None:
    """A real unit on *tools* failed here: each is absent until :func:`refresh` sees its smoke pass again."""
    path = _state(state)
    stored = _load(path)
    at = (now or datetime.now(UTC)).isoformat()
    hit = [tool for tool in tools if tool in stored]
    for tool in hit:
        stored[tool] = {**stored[tool], 'failed': at, 'why': why}
    if hit:
        _save(path, stored)
