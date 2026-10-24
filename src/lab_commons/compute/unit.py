"""A UNIT -- one calculation, named by what it computes: ``(engine, version, inputs, outputs)`` (D16, D17).

THE UNIT HASH IS THE CACHE KEY, AND IT IS EVERY FACT THE RESULT DEPENDS ON: the engine's name and version, the
entry it calls, every input FILE by the sha256 of its content (never its path or mtime) and every PARAMETER as
canonical JSON. Two units with one hash are the same computation wherever and whenever they run, so a result
computed once answers both; a unit reads no undeclared host state, which is what makes that true.

WHAT A VERSION IS. A vendor engine's version is PROBED on the host (:mod:`lab_commons.compute.presence`), never
declared there. Ours is an :class:`Environment` -- our code at a commit plus the key of the environment that
commit builds (:func:`lab_commons.hpc.builds.builds_at`: the committed lock, the install command, the
interpreter), so a host builds it once per key. A DIRTY tree is the commit plus the hash of its diff: it runs
here and nowhere else -- its result is LOCAL-ONLY, never shared, never a verdict, never shipped.

AN INPUT MAY BE ANOTHER UNIT'S OUTPUT (:class:`Output`), which is how a :class:`~lab_commons.compute.graph.Graph`
is drawn; its hash is then known only once that output exists, so :meth:`Unit.unit_hash` takes the input
files RESOLVED.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import lab_commons.launch as _door
from lab_commons.compute.model import Demand
from lab_commons.compute.presence import Presence
from lab_commons.hpc.builds import builds_at

__all__ = [
    'Engine',
    'EngineAbsent',
    'Environment',
    'Output',
    'Unit',
    'canonical',
    'content_hash',
    'environment_at',
    'environment_here',
]

_GIT: Final = shutil.which('git') or 'git'
_BLOCK: Final = 1 << 20


class EngineAbsent(LookupError):
    """A vendor engine this host has no PRESENT reading for: the message names the tool and the evidence."""


def canonical(value: object) -> bytes:
    """*value* as canonical JSON: sorted keys, no whitespace, no NaN -- one spelling per value."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def content_hash(path: Path) -> str:
    """The sha256 of a file's bytes; of a directory, over every file's relative path and content hash."""
    sha = hashlib.sha256()
    if path.is_dir():
        for one in sorted(p for p in path.rglob('*') if p.is_file()):
            sha.update(f'{one.relative_to(path).as_posix()}\0{content_hash(one)}\n'.encode())
        return sha.hexdigest()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(_BLOCK), b''):
            sha.update(block)
    return sha.hexdigest()


@dataclass(frozen=True)
class Environment:
    """Our code at *sha*, in the environment keyed *lock_hash*; a dirty tree adds the hash of its diff."""

    sha: str
    lock_hash: str
    diff_hash: str = ''

    @property
    def local_only(self) -> bool:
        """A dirty tree's environment exists on this host alone."""
        return bool(self.diff_hash)

    @property
    def key(self) -> str:
        """``<sha>:<lock hash>``, and ``+<diff hash>`` for a dirty tree."""
        return f'{self.sha}:{self.lock_hash}' + (f'+{self.diff_hash}' if self.diff_hash else '')


def _git(repo: Path, *args: str) -> str:
    return _door.run(
        [_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True
    ).stdout


def environment_at(repo: Path, sha: str, *, python: str = '') -> Environment:
    """The environment commit *sha* of *repo* builds: its key is the build key every host shares."""
    return Environment(sha, builds_at(repo, sha, python=python).env_key)


def environment_here(repo: Path, *, python: str = '') -> Environment:
    """The environment of *repo*'s working tree: HEAD's, plus the diff hash when the tree is dirty."""
    sha = _git(repo, 'rev-parse', 'HEAD').strip()
    diff = hashlib.sha256(_git(repo, 'diff', 'HEAD', '--binary').encode())
    untracked = _git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
    for name in sorted(filter(None, untracked)):
        diff.update(f'{name}\0{content_hash(repo / name)}\n'.encode())
    dirty = bool(diff.digest() != hashlib.sha256(b'').digest())
    return Environment(sha, builds_at(repo, sha, python=python).env_key, diff.hexdigest() if dirty else '')


@dataclass(frozen=True)
class Engine:
    """What computes: a vendor tool at its probed version, or our code (*environment*) at its key."""

    name: str
    version: str
    environment: Environment | None = None

    @classmethod
    def ours(cls, name: str, environment: Environment) -> Engine:
        """Our engine *name* (``<package>.<solver>``); its version IS the environment key."""
        return cls(name, environment.key, environment)

    @classmethod
    def vendor(cls, name: str, presence: Mapping[str, Presence]) -> Engine:
        """Vendor *name* at the version this host's presence reading PROBED (:func:`.presence.cached`).

        Raises:
            EngineAbsent: no present reading for *name*, or none carrying a version.

        """
        found = presence.get(name)
        if found is None or found.version is None:
            why = '; '.join(f'{e.source}: {e.value}' for e in found.evidence) if found else 'never probed'
            msg = f'engine {name!r} is not present on this host -- {why}'
            raise EngineAbsent(msg)
        return cls(name, found.version)

    @property
    def local_only(self) -> bool:
        """Ours at a dirty tree: its results never leave this host."""
        return self.environment is not None and self.environment.local_only


@dataclass(frozen=True)
class Output:
    """Input that is output *name* of the graph's unit *unit*."""

    unit: str
    name: str


@dataclass(frozen=True)
class Unit:
    """One calculation: *engine* calls *entry* (``module:function``) on *inputs*, producing files *outputs*.

    An input is a FILE (a :class:`~pathlib.Path`, or another unit's :class:`Output`) or a PARAMETER (any JSON
    value). *demand* is what the box holds while it runs, for at most *wall_s* seconds.
    """

    engine: Engine
    entry: str
    inputs: Mapping[str, Any]
    outputs: tuple[str, ...]
    demand: Demand
    wall_s: int

    def __post_init__(self) -> None:
        """Refuse an entry that is not ``module:function`` and a parameter JSON cannot spell."""
        if ':' not in self.entry:
            msg = f'unit entry {self.entry!r} is not `module:function`'
            raise ValueError(msg)
        canonical(self.params)  # refuses a parameter JSON cannot spell, at declaration

    @property
    def params(self) -> dict[str, Any]:
        """The inputs that are parameters (JSON values), not files."""
        return {k: v for k, v in self.inputs.items() if not isinstance(v, Path | Output)}

    @property
    def version(self) -> str:
        """The engine's version -- one fact, held by the engine."""
        return self.engine.version

    @property
    def upstream(self) -> frozenset[str]:
        """The graph units whose outputs this one reads."""
        return frozenset(v.unit for v in self.inputs.values() if isinstance(v, Output))

    def unit_hash(self, files: Mapping[str, Path]) -> str:
        """The cache key, over the input *files* resolved (every file input named, each hashed by content)."""
        wanted = {k for k, v in self.inputs.items() if isinstance(v, Path | Output)}
        if set(files) != wanted:
            msg = f'unit hash needs exactly the file inputs {sorted(wanted)}, got {sorted(files)}'
            raise ValueError(msg)
        facts = {
            'engine': self.engine.name,
            'version': self.version,
            'entry': self.entry,
            'files': {name: content_hash(path) for name, path in files.items()},
            'params': self.params,
        }
        return hashlib.sha256(canonical(facts)).hexdigest()
