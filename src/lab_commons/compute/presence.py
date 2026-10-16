"""IS A VENDOR TOOL REALLY THERE -- several sources, one verdict, with its evidence.

A tool is PRESENT on a host only when all four agree:

1. its executable is FOUND -- a declared path (an environment variable the tool's own docs name), a
   standard install directory, the registry or ``PATH``;
2. its VERSION reads (``--version``, the executable's file version, its install path, its package
   metadata) and EQUALS the version the serving repo declares ONCE for the family, in its
   ``pyproject.toml`` ``[tool.lab_commons.tools]`` (``<tool> = "<version>"``) -- vendor engines are
   uniform by policy, so a host is never the place a version is declared; none declared is absent;
3. a SMOKE run passes -- the tool does one trivial thing end to end (:data:`~lab_commons.compute.toolchecks.CHECKS`);
4. no real unit on that tool has failed on this host since that smoke (:func:`demote`).

Each step leaves :class:`Evidence` (source, value, time), and the host card shows it
(:mod:`lab_commons.dev.forgehosts`): the card is the probe's reading, never a declaration. Seat counts
are the one thing a person declares (:mod:`lab_commons.box.machine`), because no probe can read a licence.

A smoke costs seconds to minutes (MATLAB starting), so a reading is CACHED for :data:`TTL` in
``tool-presence.json`` beside the machine file. :func:`refresh` re-probes what is due -- older than the
TTL, or demoted since its smoke -- and ``forge serve`` calls it once per tick; :func:`cached` only reads,
and a reading past its TTL is absent, so a host nobody refreshes stops claiming. All readers of the OS
are module functions behind :class:`~lab_commons.compute.toolchecks.Check`
(:mod:`lab_commons.compute.toolchecks`), so a test passes fakes.
"""

from __future__ import annotations

import json
import re
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
    'cached',
    'declared_versions',
    'demote',
    'probe',
    'refresh',
]

#: How long one probe's reading holds before :func:`refresh` runs it again and :func:`cached` reads it absent.
TTL: Final = timedelta(hours=6)


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


# --- the verdict ---------------------------------------------------------------------------------------


def declared_versions(root: Path) -> dict[str, str]:
    """The serving repo's ``[tool.lab_commons.tools]``: ``{tool: version}``; a non-string version is refused."""
    manifest = root / 'pyproject.toml'
    if not manifest.is_file():
        return {}
    table = tomllib.loads(manifest.read_text(encoding='utf-8')).get('tool', {}).get('lab_commons', {}).get('tools', {})
    if bad := sorted(name for name, version in table.items() if not isinstance(version, str)):
        msg = f'{manifest} [tool.lab_commons.tools]: {bad} must each be a version string, e.g. femm = "5.0"'
        raise ValueError(msg)
    return dict(table)


def probe(tool: str, check: Check, declared: str | None, *, now: datetime) -> Presence:
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
    if declared is None:
        return Presence(
            tool, present=False, evidence=(*seen, Evidence('version', 'none declared in [tool.lab_commons.tools]', at))
        )
    if not _matches(version[1], declared):
        return Presence(tool, present=False, evidence=(*seen, Evidence('version', f'declared {declared}', at)))
    passed, detail = check.smoke(path)
    return Presence(
        tool, present=passed, evidence=(*seen, Evidence('smoke', f'{"passed" if passed else "FAILED"}: {detail}', at))
    )


def _matches(read: str, declared: str) -> bool:
    """The read version names the declared one: equal, or a whole token of it (a leading ``v`` dropped)."""
    tokens = {token.removeprefix('v') for token in re.split(r'[^\w.]+', read)}
    return read == declared or declared in tokens


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


def _current(entry: Mapping[str, Any], now: datetime, ttl: timedelta) -> bool:
    """A reading still holds: inside its TTL and no real unit failed since it was taken."""
    probed = datetime.fromisoformat(entry['probed'])
    failed = entry.get('failed')
    return now - probed < ttl and (failed is None or datetime.fromisoformat(failed) < probed)


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
    versions: Mapping[str, str],
    *,
    checks: Mapping[str, Check] = CHECKS,
    state: Path | None = None,
    now: datetime | None = None,
    ttl: timedelta = TTL,
) -> dict[str, Presence]:
    """Re-probe every tool whose reading is due (stale, demoted or missing) against *versions*; then :func:`cached`."""
    now = now or datetime.now(UTC)
    path = _state(state)
    stored = _load(path)
    due = [tool for tool in checks if tool not in stored or not _current(stored[tool], now, ttl)]
    for tool in due:
        stored[tool] = {**probe(tool, checks[tool], versions.get(tool), now=now).row(), 'probed': now.isoformat()}
    if due:
        _save(path, stored)
    return cached(checks=checks, state=path, now=now, ttl=ttl)


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
