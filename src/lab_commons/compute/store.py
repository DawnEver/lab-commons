"""THE RESULT STORE -- a unit's outputs, content-addressed by its unit hash; the first result wins (D16, D18, D19).

TWO TIERS, ONE RULE. The LOCAL tier is a directory per unit hash (``<root>/<hh>/<unit hash>/``: the declared
output files and ``record.json``), installed by ONE atomic rename of a fully written staging directory, so a
crash leaves the whole result or none. The SHARED tier is whatever :class:`SharedTier` the caller hands in --
the forge's (:class:`dev_lab.dev.forgeunit.ForgeResults`), whose writes go through the durable outbox.

THE FIRST RESULT FOR A UNIT HASH WINS. Units are deterministic, so a duplicate run (a lost lease, a partition)
wastes time but cannot corrupt: an identical later result is dropped, and a DIFFERING one raises
:class:`Nondeterministic` -- non-determinism found, which the shared tier also sounds as an alarm.

A LOCAL-ONLY RESULT (a unit of a dirty tree) NEVER LEAVES THE BOX: it is never posted, and the hash it is keyed
by -- which carries the diff hash -- is one no other host can ask for.

FILES ARE THE PAYLOAD. :meth:`ResultStore.put_files` stages named files (bytes, or a path copied) and puts them
like any result, its ``result_hash`` derived from their sha256s, so first-wins and :class:`Nondeterministic`
compare content; :attr:`Result.files` names them in the local tier. A result FETCHED from the shared tier is
re-hashed before it installs: outputs that do not hash to their record raise :class:`ArrivalMismatch` and
nothing installs. A tier declares ``max_bytes`` (its upload limit; ``None``: none). A shareable result over it
is posted with its largest files moved, one by one until it fits, to the :class:`LargeObjects` store the caller
handed in: each travels as ``<name>`` + :data:`REF_SUFFIX`, a :class:`Ref` (backend, key, size, sha256), and an
arrival resolves every Ref and re-checks its sha256 before the result hash is checked -- a mismatch, or a Ref
with no large-object store to resolve it, is :class:`ArrivalMismatch`, a miss and never a value. With no
large-object store (or still over the limit with every file moved) it installs locally and raises
:class:`TooLarge` -- it is never truncated.

A TOMBSTONE INVALIDATES; NOTHING IS DELETED. :meth:`ResultStore.invalidate` moves the live result, with a
:class:`Tombstone` naming the reason, to ``<root>/<hh>/<unit hash>.graves/<generation>/``; a later get answers
the tombstone (a miss with its reason) until a new result is put. Each tombstone opens the next GENERATION and
first-wins holds per generation, so the re-put after it is a new result, not a non-determinism. The shared tier
carries the tombstones (``bury`` / ``graves``) and posts per generation, and every get, put and invalidate first
applies the tier's graves this host has not seen -- an invalidation on one host is seen on all.

THE LOCAL TIER IS A CACHE BOUNDED BY ITS VOLUME'S FREE-SPACE FLOOR (``free_floor_bytes``; ``None``: unbounded;
D54). Every hit -- a local get, an arrival from the shared tier, a put -- stamps the result's recency; after each
install :func:`lab_commons.compute.floor.keep` evicts the least recently hit results, never the one just installed,
until the volume keeps the floor free -- and if it cannot, the install is undone and refused
(:class:`~lab_commons.compute.floor.BelowFloor`). Eviction removes a live copy only (renamed out of place first, so
a crash never leaves half a result): an evicted unit is a miss here and a shareable one comes back from the shared
tier; graves are never evicted. The floor is the caller's -- the machine's ``[storage] free_floor_gb``
(:func:`lab_commons.storage.free_floor_bytes`) -- never a value in this module. The fixed ``cap_bytes`` is retired.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from lab_commons.compute import floor
from lab_commons.compute.durable import durable_write
from lab_commons.compute.unit import canonical, content_hash

__all__ = [
    'INSTALL_BACKOFF_S',
    'RECORD',
    'REF_SUFFIX',
    'TOMBSTONE',
    'ArrivalMismatch',
    'LargeObjects',
    'Nondeterministic',
    'Record',
    'Ref',
    'Result',
    'ResultStore',
    'SharedTier',
    'Tombstone',
    'TooLarge',
    'result_hash',
]

#: The record's file name inside a result directory; never an output name.
RECORD: Final = 'record.json'
#: A grave's tombstone file name, beside the buried result's ``result/`` directory.
TOMBSTONE: Final = 'tombstone.json'
#: The suffix a file shared by :class:`Ref` travels under (``<name>.ref.json``); never a payload name's ending.
REF_SUFFIX: Final = '.ref.json'

#: The waits, in seconds, before each retry of an install rename that a foreign handle refused: Windows denies a
#: directory rename (WinError 5 / 32) while an indexer, a scanner or a just-exited child holds a handle inside it,
#: and that hold is transient. Six retries, 7.75 s of waiting in total, then the refusal is raised.
INSTALL_BACKOFF_S: Final = (0.25, 0.5, 1.0, 1.0, 2.0, 3.0)


class Nondeterministic(RuntimeError):
    """One unit hash, two different results: the first stands; the message names both."""


class ArrivalMismatch(ValueError):
    """A result fetched from the shared tier whose outputs do not hash to its record: refused, nothing installed."""


class TooLarge(ValueError):
    """A shareable result over the shared tier's upload limit: installed locally, never posted, never truncated."""

    def __init__(self, result: Result, size: int, limit: int) -> None:
        """*result* weighs *size* bytes against the tier's *limit*."""
        self.result, self.size, self.limit = result, size, limit
        super().__init__(
            f'unit {result.record.unit_hash[:16]}: result is {size} bytes, over the shared tier limit of {limit} '
            f'bytes; it is kept locally at {result.path} and not shared'
        )


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

    @property
    def files(self) -> dict[str, Path]:
        """Every output, by name, in this tier."""
        return {one.name: one for one in sorted(self.path.iterdir()) if one.name != RECORD}


@dataclass(frozen=True)
class Tombstone:
    """Generation *generation* of *unit_hash* is invalidated: its result (*result_hash*; ``''``: none) and why."""

    unit_hash: str
    generation: int
    result_hash: str
    reason: str
    host: str
    when: str

    def to_bytes(self) -> bytes:
        """The tombstone as canonical JSON."""
        return canonical(asdict(self))

    @classmethod
    def read(cls, path: Path) -> Tombstone:
        """The tombstone written at *path*."""
        return cls(**json.loads(path.read_text(encoding='utf-8')))


def result_hash(directory: Path, outputs: tuple[str, ...]) -> str:
    """The hash of the *outputs* in *directory*, by content."""
    return hashlib.sha256(canonical({name: content_hash(directory / name) for name in outputs})).hexdigest()


class SharedTier(Protocol):
    """The tier every host reads: results per generation, and the tombstones that close generations."""

    #: The largest result, in bytes, the tier uploads; ``None``: no limit.
    max_bytes: int | None

    def fetch(self, unit_hash: str, generation: int, dest: Path) -> Path | None:
        """Download *generation*'s first result for *unit_hash* into *dest* (outputs + record); ``None``: none."""

    def post(self, result: Result, generation: int) -> None:
        """Post *result* as *generation*'s: an identical one there is a no-op, a differing one Nondeterministic."""

    def bury(self, tombstone: Tombstone) -> None:
        """Post *tombstone*; one already there for its generation is a no-op."""

    def graves(self, unit_hash: str) -> tuple[Tombstone, ...]:
        """Every tombstone posted for *unit_hash*, by generation."""


@dataclass(frozen=True)
class Ref:
    """One file kept in a large-object store: which store, its key there, its size and its sha256."""

    backend: str
    ref: str
    bytes: int
    sha256: str

    def to_bytes(self) -> bytes:
        """The ref as canonical JSON."""
        return canonical(asdict(self))

    @classmethod
    def read(cls, path: Path) -> Ref:
        """The ref written at *path*."""
        return cls(**json.loads(path.read_text(encoding='utf-8')))


class LargeObjects(Protocol):
    """Where files too large for the shared tier live; the caller hands one in (its backend is its config)."""

    def put(self, path: Path) -> Ref:
        """Upload *path*; the same bytes again is the object already there."""

    def get(self, ref: Ref, dest: Path) -> Path:
        """Download *ref*'s bytes to *dest*; the store checks them, the caller checks them again."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


class ResultStore:
    """The local tier at *root*, backed by *shared* (none: this host's results only)."""

    def __init__(
        self,
        root: Path,
        shared: SharedTier | None = None,
        *,
        free_floor_bytes: int | None = None,
        objects: LargeObjects | None = None,
        **retired: object,
    ) -> None:
        """Results under *root*; *shared* is asked for a miss and handed every shareable put.

        *free_floor_bytes* is the free space the store's volume keeps (``None``: unbounded); under it, the least
        recently hit results go. *objects* holds the files of a shareable result over the shared tier's limit
        (``None``: such a result is :class:`TooLarge`).
        """
        if retired:
            name = min(retired)
            msg = f'ResultStore() got an unexpected keyword argument {name!r}'
            if name == 'cap_bytes':
                msg = (
                    "cap_bytes is retired (D54): a cache is bounded by its volume's free-space floor -- pass "
                    'free_floor_bytes (lab_commons.storage.free_floor_bytes())'
                )
            raise TypeError(msg)
        self.root, self.shared, self.free_floor_bytes, self.objects = root, shared, free_floor_bytes, objects
        self._clock = 0

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

    def _graves(self, unit_hash: str) -> Path:
        return self.root / unit_hash[:2] / f'{unit_hash}.graves'

    def history(self, unit_hash: str) -> tuple[tuple[Tombstone, Result | None], ...]:
        """Every tombstone here for *unit_hash*, by generation, with the result it buried (``None``: none)."""
        graves = self._graves(unit_hash)
        found = sorted((int(g.name), g) for g in graves.iterdir() if g.name.isdigit()) if graves.is_dir() else []
        return tuple(
            (
                Tombstone.read(grave / TOMBSTONE),
                Result(grave / 'result', Record.read(grave / 'result' / RECORD))
                if (grave / 'result' / RECORD).is_file()
                else None,
            )
            for _, grave in found
        )

    def generation(self, unit_hash: str) -> int:
        """The generation a result for *unit_hash* is put in here: the number of tombstones before it."""
        return len(self.history(unit_hash))

    def _bury(self, tombstone: Tombstone) -> None:
        """Move the live result, with *tombstone*, into its grave; one rename installs the whole grave."""
        grave = self.staging()
        durable_write(grave / TOMBSTONE, tombstone.to_bytes())
        live = self.path(tombstone.unit_hash)
        if (live / RECORD).is_file():
            live.rename(grave / 'result')
        target = self._graves(tombstone.unit_hash) / str(tombstone.generation)
        target.parent.mkdir(parents=True, exist_ok=True)
        grave.rename(target)

    def _sync(self, unit_hash: str) -> None:
        """Apply the shared tier's tombstones for *unit_hash* that this host has not seen."""
        if self.shared is None:
            return
        for tombstone in self.shared.graves(unit_hash):
            if tombstone.generation == self.generation(unit_hash):
                self._bury(tombstone)

    def get(self, unit_hash: str) -> Result | Tombstone | None:
        """The live result for *unit_hash*: this host's, else the shared tier's (verified, installed here).

        A miss is the latest :class:`Tombstone` when the unit was invalidated, else ``None``.

        Raises:
            ArrivalMismatch: the shared tier's outputs do not hash to their record.

        """
        self._sync(unit_hash)
        found = self._local(unit_hash)
        if found is not None:
            self._hit(found)
            return found
        generation = self.generation(unit_hash)
        if self.shared is not None:
            dest = self.staging()
            staged = self.shared.fetch(unit_hash, generation, dest)
            if staged is not None:
                return self._install(staged, self._arrived(unit_hash, staged))
            shutil.rmtree(dest, ignore_errors=True)
        return self.history(unit_hash)[-1][0] if generation else None

    def _resolve(self, unit_hash: str, staged: Path) -> None:
        """Replace each Ref in *staged* by its bytes from the large-object store, sha256-checked; else refuse."""
        for held in sorted(staged.glob(f'*{REF_SUFFIX}')):
            name = held.name.removesuffix(REF_SUFFIX)
            why = ''
            if self.objects is None:
                why = 'it carries a Ref and no large-object store was given to this store'
            else:
                ref = Ref.read(held)
                got = _sha256(self.objects.get(ref, staged / name))
                if got != ref.sha256:
                    why = f'its bytes arrived hashing {got[:16]}, the Ref says {ref.sha256[:16]}'
            if why:
                shutil.rmtree(staged, ignore_errors=True)
                msg = f'unit {unit_hash[:16]}: shared file {name!r}: {why}; refused, nothing installed'
                raise ArrivalMismatch(msg)
            held.unlink()

    def _arrived(self, unit_hash: str, staged: Path) -> Record:
        """The record of a fetched result whose outputs (Refs resolved) hash to it; else drop *staged* and refuse."""
        self._resolve(unit_hash, staged)
        record = Record.read(staged / RECORD)
        got = result_hash(staged, tuple(sorted(one.name for one in staged.iterdir() if one.name != RECORD)))
        if record.unit_hash != unit_hash or got != record.result_hash:
            shutil.rmtree(staged, ignore_errors=True)
            msg = (
                f'unit {unit_hash[:16]}: the shared result arrived hashing {got[:16]} (record: unit '
                f'{record.unit_hash[:16]}, result {record.result_hash[:16]}); refused, nothing installed'
            )
            raise ArrivalMismatch(msg)
        return record

    def put(self, staged: Path, record: Record) -> Result:
        """Install the outputs in *staged* as *record*'s result, then share it unless it is local-only.

        Raises:
            Nondeterministic: a result with other outputs is already stored for the unit hash in this generation.
            TooLarge: shareable but over the shared tier's ``max_bytes`` even with its largest files moved to the
                large-object store (or with none given); installed here, not posted.

        """
        self._sync(record.unit_hash)
        durable_write(staged / RECORD, record.to_bytes())
        result = self._install(staged, record)
        if self.shared is not None and not record.local_only:
            limit = self.shared.max_bytes
            size = sum(one.stat().st_size for one in result.path.rglob('*') if one.is_file())
            if limit is None or size <= limit:
                self.shared.post(result, self.generation(record.unit_hash))
            elif self.objects is None:
                raise TooLarge(result, size, limit)
            else:
                self._post_by_ref(self.shared, self.objects, result, (size, limit))
        return result

    def _post_by_ref(self, shared: SharedTier, objects: LargeObjects, result: Result, sizes: tuple[int, int]) -> None:
        """Post *result* to *shared* with its largest files as Refs in *objects* until it fits (size, limit)."""
        size, limit = sizes
        view = self.staging()
        shutil.copyfile(result.path / RECORD, view / RECORD)
        for name, path in sorted(result.files.items(), key=lambda kv: -kv[1].stat().st_size):
            if size > limit:
                ref = objects.put(path).to_bytes()
                (view / f'{name}{REF_SUFFIX}').write_bytes(ref)
                size -= path.stat().st_size - len(ref)
            else:
                shutil.copyfile(path, view / name)
        try:
            if size > limit:
                raise TooLarge(result, size, limit)
            shared.post(Result(view, result.record), self.generation(result.record.unit_hash))
        finally:
            shutil.rmtree(view, ignore_errors=True)

    def put_files(self, files: Mapping[str, bytes | Path], record: Record) -> Result:
        """Put *files* (name -> bytes, or a file to copy) as *record*'s result; its ``result_hash`` is theirs.

        Raises:
            ValueError: a file name that is not one plain name, or is the record's.
            Nondeterministic: other files are already stored for the unit hash in this generation.
            TooLarge: as :meth:`put`.

        """
        for name in files:
            if name in {'', '.', '..', RECORD} or any(sep in name for sep in '/\\') or name.endswith(REF_SUFFIX):
                msg = (
                    f'file name {name!r}: a payload file is one plain name, never {RECORD!r}, a path or a name '
                    f'ending {REF_SUFFIX!r} (the suffix a shared Ref travels under)'
                )
                raise ValueError(msg)
        staged = self.staging()
        for name, content in files.items():
            if isinstance(content, Path):
                shutil.copyfile(content, staged / name)
            else:
                (staged / name).write_bytes(content)
        return self.put(staged, replace(record, result_hash=result_hash(staged, tuple(sorted(files)))))

    def invalidate(self, unit_hash: str, reason: str, *, host: str) -> Tombstone:
        """Tombstone *unit_hash*'s live result for *reason*, here and (unless local-only) on the shared tier."""
        self._sync(unit_hash)
        live = self._local(unit_hash)
        tombstone = Tombstone(
            unit_hash,
            self.generation(unit_hash),
            '' if live is None else live.record.result_hash,
            reason,
            host,
            datetime.now(UTC).isoformat(timespec='seconds'),
        )
        self._bury(tombstone)
        if self.shared is not None and (live is None or not live.record.local_only):
            self.shared.bury(tombstone)
        return tombstone

    def _install(self, staged: Path, record: Record) -> Result:
        """Rename *staged* into place; a foreign handle's refusal is retried per INSTALL_BACKOFF_S."""
        target = self.path(record.unit_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        for wait in (*INSTALL_BACKOFF_S, None):
            try:
                staged.rename(target)
                return self._kept(Result(target, record))
            except OSError as refused:
                held = self._local(record.unit_hash)
                if held is not None:
                    return self._kept(self._first_stands(staged, record, held))
                if not isinstance(refused, PermissionError):
                    raise
                if wait is None:
                    msg = (
                        f'install of {staged} -> {target} still refused after {len(INSTALL_BACKOFF_S)} retries '
                        f'over {sum(INSTALL_BACKOFF_S)} s: {refused}'
                    )
                    raise PermissionError(refused.errno, msg) from refused
                time.sleep(wait)
        raise AssertionError  # unreachable: the last pass returns or raises

    def _hit(self, result: Result) -> None:
        """Stamp *result* most recently hit: its record's mtime, strictly after every stamp this store made."""
        self._clock = max(time.time_ns(), self._clock + 1)
        os.utime(result.path / RECORD, ns=(self._clock, self._clock))

    def _live(self) -> list[tuple[int, Path, int]]:
        """Every live result here: (last hit, directory, bytes); never a grave or a staging directory."""
        found = []
        for record in self.root.glob(f'*/*/{RECORD}'):
            held = record.parent
            if held.parent.name != held.name[:2] or held.name.endswith('.graves'):
                continue
            size = sum(one.stat().st_size for one in held.rglob('*') if one.is_file())
            found.append((record.stat().st_mtime_ns, held, size))
        return found

    def _kept(self, result: Result) -> Result:
        """Stamp *result* hit, then evict the least recently hit others until the volume keeps the floor.

        Raises:
            BelowFloor: the floor does not hold with every other result evicted; *result* is evicted too.

        """
        self._hit(result)
        if self.free_floor_bytes is None:
            return result
        try:
            floor.keep(self.root, self._live(), self.free_floor_bytes, evict=self._evict, keep=result.path)
        except floor.BelowFloor:
            self._evict(result.path)
            raise
        return result

    def _evict(self, held: Path) -> None:
        """Remove the live result *held*: renamed out of place first, so a crash never leaves half of it."""
        gone = self.staging()
        held.rename(gone / 'evicted')
        shutil.rmtree(gone)

    @staticmethod
    def _first_stands(staged: Path, record: Record, held: Result) -> Result:
        """Drop *staged* for the *held* result; one with other outputs raises Nondeterministic."""
        shutil.rmtree(staged, ignore_errors=True)
        if held.record.result_hash != record.result_hash:
            msg = (
                f'unit {record.unit_hash[:16]}: result {record.result_hash[:16]} from {record.host} differs '
                f'from the stored {held.record.result_hash[:16]} from {held.record.host}; the first stands'
            )
            raise Nondeterministic(msg) from None
        return held
