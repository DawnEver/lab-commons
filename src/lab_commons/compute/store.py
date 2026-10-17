"""THE RESULT STORE -- a unit's outputs, content-addressed by its unit hash; the first result wins (D16, D18, D19).

TWO TIERS, ONE RULE. The LOCAL tier is a directory per unit hash (``<root>/<hh>/<unit hash>/``: the declared
output files and ``record.json``), installed by ONE atomic rename of a fully written staging directory, so a
crash leaves the whole result or none. The SHARED tier is whatever :class:`SharedTier` the caller hands in --
the forge's (:class:`lab_commons.dev.forgeunit.ForgeResults`), whose writes go through the durable outbox.

THE FIRST RESULT FOR A UNIT HASH WINS. Units are deterministic, so a duplicate run (a lost lease, a partition)
wastes time but cannot corrupt: an identical later result is dropped, and a DIFFERING one raises
:class:`Nondeterministic` -- non-determinism found, which the shared tier also sounds as an alarm.

A LOCAL-ONLY RESULT (a unit of a dirty tree) NEVER LEAVES THE BOX: it is never posted, and the hash it is keyed
by -- which carries the diff hash -- is one no other host can ask for.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Protocol

from lab_commons.compute.unit import canonical, content_hash
from lab_commons.dev._forge_journal import durable_write

__all__ = ['RECORD', 'Nondeterministic', 'Record', 'Result', 'ResultStore', 'SharedTier', 'result_hash']

#: The record's file name inside a result directory; never an output name.
RECORD: Final = 'record.json'


class Nondeterministic(RuntimeError):
    """One unit hash, two different results: the first stands; the message names both."""


@dataclass(frozen=True)
class Record:
    """What computed a result, where and when -- and the hash of the outputs it made."""

    unit_hash: str
    engine: str
    version: str
    environment: str
    host: str
    started: str
    finished: str
    result_hash: str
    local_only: bool

    def to_bytes(self) -> bytes:
        """The record as canonical JSON."""
        return canonical(asdict(self))

    @classmethod
    def read(cls, path: Path) -> Record:
        """The record written at *path*."""
        return cls(**json.loads(path.read_text(encoding='utf-8')))


@dataclass(frozen=True)
class Result:
    """One installed result: its directory and its record."""

    path: Path
    record: Record

    def output(self, name: str) -> Path:
        """The output file *name*."""
        return self.path / name


def result_hash(directory: Path, outputs: tuple[str, ...]) -> str:
    """The hash of the *outputs* in *directory*, by content."""
    return hashlib.sha256(canonical({name: content_hash(directory / name) for name in outputs})).hexdigest()


class SharedTier(Protocol):
    """The tier every host reads: fetch a posted result, post one."""

    def fetch(self, unit_hash: str, dest: Path) -> Path | None:
        """Download the first result posted for *unit_hash* into *dest* (outputs + record); ``None`` when none."""

    def post(self, result: Result) -> None:
        """Post *result*; an identical one already there is a no-op, a differing one raises Nondeterministic."""


class ResultStore:
    """The local tier at *root*, backed by *shared* (none: this host's results only)."""

    def __init__(self, root: Path, shared: SharedTier | None = None) -> None:
        """Results under *root*; *shared* is asked for a miss and handed every shareable put."""
        self.root, self.shared = root, shared

    def path(self, unit_hash: str) -> Path:
        """Where *unit_hash*'s result lives here."""
        return self.root / unit_hash[:2] / unit_hash

    def staging(self) -> Path:
        """A fresh directory on the store's own filesystem, so an install is one rename."""
        staged = self.root / '.staging' / uuid.uuid4().hex
        staged.mkdir(parents=True)
        return staged

    def _local(self, unit_hash: str) -> Result | None:
        record = self.path(unit_hash) / RECORD
        return Result(record.parent, Record.read(record)) if record.is_file() else None

    def get(self, unit_hash: str) -> Result | None:
        """The result for *unit_hash*: this host's, else the shared tier's (installed here), else ``None``."""
        found = self._local(unit_hash)
        if found is not None or self.shared is None:
            return found
        staged = self.shared.fetch(unit_hash, self.staging())
        return None if staged is None else self._install(staged, Record.read(staged / RECORD))

    def put(self, staged: Path, record: Record) -> Result:
        """Install the outputs in *staged* as *record*'s result, then share it unless it is local-only.

        Raises:
            Nondeterministic: a result with other outputs is already stored for the unit hash.

        """
        durable_write(staged / RECORD, record.to_bytes())
        result = self._install(staged, record)
        if self.shared is not None and not record.local_only:
            self.shared.post(result)
        return result

    def _install(self, staged: Path, record: Record) -> Result:
        target = self.path(record.unit_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            staged.rename(target)
        except OSError:
            held = self._local(record.unit_hash)
            if held is None:
                raise
            shutil.rmtree(staged, ignore_errors=True)
            if held.record.result_hash != record.result_hash:
                msg = (
                    f'unit {record.unit_hash[:16]}: result {record.result_hash[:16]} from {record.host} differs '
                    f'from the stored {held.record.result_hash[:16]} from {held.record.host}; the first stands'
                )
                raise Nondeterministic(msg) from None
            return held
        return Result(target, record)
