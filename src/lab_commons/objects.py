"""OBJECTS -- the family's content-addressed object store on an rclone remote (:class:`RcloneObjects`).

ONE client for every repo that keeps bytes by sha256 on a remote (the run store's oversize results, a case's locked
inputs and vendor results): an object lives once under ``<remote>/<prefix>/<sha[:2]>/<sha>``, written
``--immutable``. Every byte is re-hashed where it arrives, both ways:

* :meth:`RcloneObjects.put` uploads, then re-hashes what the remote holds; other bytes are removed and refused
  (:class:`UploadUnverified`), so a bad object is never there to be found.
* :meth:`RcloneObjects.fetch` takes the sha256 a lock names and refuses one the lock does not name
  (:class:`NotLocked`) before the remote is asked; :meth:`RcloneObjects.get` takes a
  :class:`~lab_commons.compute.store.Ref`. A mismatch on arrival is the store's own
  :class:`~lab_commons.compute.store.ArrivalMismatch`, and no file is left at or beside the destination.
* :meth:`RcloneObjects.verify` re-hashes a stored object in place of trusting it.

WHICH store is the family's fact (``[storage] runs``, :mod:`lab_commons.storage`): :func:`declared_objects`
derives it, with the machine's pinned rclone (:func:`rclone_exe`, under :func:`lab_commons.home.pinned_bin`) and the
config dev-lab renders (:data:`RCLONE_CONFIG`) -- so a product needs no dev-lab import. Either absent is refused by
name with its door (:class:`RcloneAbsent`, :class:`ConfigUnrendered`); ``PATH`` is never guessed.

A PACK (v1 ruling D125 F7) carries objects to a place with no store: :func:`pack` zips named files with a
``manifest.toml`` (:data:`MANIFEST`) naming each member's relative path, sha256 and size, each distinct content
stored ONCE under ``objects/<sha[:2]>/<sha>``; :func:`unpack` re-hashes every byte and refuses a pack that lies
(:class:`PackRefused`) before writing anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
import zipfile
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from lab_commons import home as _home
from lab_commons import launch as _door
from lab_commons import storage as _storage
from lab_commons.compute.store import ArrivalMismatch, Ref

__all__ = [
    'MANIFEST',
    'OBJECTS_PREFIX',
    'PACK_FORMAT',
    'PACK_OBJECTS',
    'RCLONE',
    'RCLONE_CONFIG',
    'SCRATCH_PREFIX',
    'ConfigUnrendered',
    'Member',
    'NotLocked',
    'ObjectRefused',
    'PackRefused',
    'RcloneAbsent',
    'RcloneObjects',
    'UploadUnverified',
    'declared_objects',
    'file_sha256',
    'pack',
    'rclone_exe',
    'read_manifest',
    'unpack',
]

#: A pack's manifest member name.
MANIFEST: Final = 'manifest.toml'
#: The manifest format this reader reads and the writer writes.
PACK_FORMAT: Final = 1
#: The zip directory a pack's content-addressed objects live under.
PACK_OBJECTS: Final = 'objects'
#: The durable key prefix every run's objects live under on the declared store.
OBJECTS_PREFIX: Final = 'results'
#: The first key segment a remote may age out (a lifecycle rule deletes it): never a durable object's place.
SCRATCH_PREFIX: Final = 'scratch'
#: The backend name a :class:`~lab_commons.compute.store.Ref` of this store carries.
RCLONE: Final = 'rclone'
#: Where dev-lab's ``storage render`` writes this machine's rclone remotes (owner-only, outside every repo).
RCLONE_CONFIG: Final = Path('~/.config/lab-commons/rclone.conf')

_SHA256: Final = re.compile(r'[0-9a-f]{64}')
_FORBIDDEN: Final = frozenset('"*:<>?\\|')
_BLOCK: Final = 1 << 20
_RCLONE_TIMEOUT_S: Final = 3600


class ObjectRefused(ValueError):
    """An object operation refused: a malformed sha256 or prefix, no rclone named, or the remote's own refusal."""


class PackRefused(ObjectRefused):
    """A pack that cannot be written or does not verify; the message names the member and why."""


class NotLocked(ObjectRefused):
    """A fetch of a sha256 the caller's lock does not name: only lock-named objects are read."""


class UploadUnverified(ObjectRefused):
    """An upload whose stored bytes do not re-hash to the file's sha256; the object was removed."""


class RcloneAbsent(ObjectRefused):
    """The pinned rclone is not placed on this machine: the message names its install door."""


class ConfigUnrendered(ObjectRefused):
    """No rendered rclone config on this machine: the message names the render door."""


def rclone_exe() -> Path:
    """The pinned rclone under :func:`lab_commons.home.pinned_bin`; absent raises :class:`RcloneAbsent`."""
    exe = _home.pinned_bin() / ('rclone.exe' if os.name == 'nt' else 'rclone')
    if not exe.is_file():
        msg = (
            f'the pinned rclone is not at {exe} (a PATH rclone is not the pinned one): '
            '`python -m dev_lab.dev.toolrelease install rclone`'
        )
        raise RcloneAbsent(msg)
    return exe


def file_sha256(path: Path) -> str:
    """The sha256 of the file *path*, read in MB blocks."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(_BLOCK), b''):
            digest.update(block)
    return digest.hexdigest()


def _check_prefix(prefix: str) -> None:
    problems = []
    if prefix.split('/', 1)[0] == SCRATCH_PREFIX:
        problems.append(f'under {SCRATCH_PREFIX}/, which a bucket ages out -- a durable object never lives there')
    if not prefix.isascii():
        problems.append('non-ASCII')
    for part in prefix.split('/'):
        if not part:
            problems.append('an empty segment')
        elif _FORBIDDEN & set(part) or not part.isprintable():
            problems.append(f'{part!r} holds a character among {"".join(sorted(_FORBIDDEN))}')
    if problems:
        msg = f'object prefix {prefix!r} refused: {"; ".join(problems)}'
        raise ObjectRefused(msg)


@dataclass(frozen=True)
class RcloneObjects:
    """A :class:`~lab_commons.compute.store.LargeObjects` on an rclone remote, content-addressed and re-hashed.

    *rclone* is the argv of the pinned rclone plus its ``--config=`` (:func:`declared_objects` builds it); *prefix*
    is durable: a first segment ``scratch`` is refused.
    """

    remote: str
    prefix: str
    rclone: Sequence[str]
    backend: str = RCLONE

    def __post_init__(self) -> None:
        """Refuse a prefix a remote cannot hold or a bucket ages out, and an unnamed rclone."""
        _check_prefix(self.prefix)
        if not self.rclone:
            msg = (
                'no rclone named: pass the pinned rclone argv (dev-lab resolves it, '
                '`python -m dev_lab.dev.storage check <remote>` proves it) -- never a PATH guess'
            )
            raise ObjectRefused(msg)

    def at(self, key: str) -> str:
        """The remote path of *key*; a bare ``name:`` takes *key* relative, never as an absolute ``name:/key``."""
        return f'{self.remote}{key}' if self.remote.endswith(':') else f'{self.remote.rstrip("/")}/{key}'

    def key(self, sha256: str) -> str:
        """``<prefix>/<sha[:2]>/<sha>``: where the object of *sha256* lives; a malformed sha256 is refused."""
        if _SHA256.fullmatch(sha256) is None:
            msg = f'{sha256!r} is not a sha256 (64 lowercase hex digits)'
            raise ObjectRefused(msg)
        return f'{self.prefix}/{sha256[:2]}/{sha256}'

    def _run(self, *args: str, ok: Sequence[int] = (0,)) -> subprocess.CompletedProcess[str]:
        done = _door.run(
            [*self.rclone, *args], capture_output=True, text=True, encoding='utf-8', errors='replace', check=False,
            timeout=_RCLONE_TIMEOUT_S,
        )  # fmt: skip
        if done.returncode not in ok:
            msg = f'rclone {args[0]} exited {done.returncode}: {done.stderr.strip()[-300:]}'
            raise ObjectRefused(msg)
        return done

    def _listed(self, key: str) -> dict[str, Any] | None:
        folder, _, name = key.rpartition('/')
        done = self._run('lsjson', '--files-only', self.at(folder), ok=(0, 3))  # 3: directory not found
        items = json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else []
        return next((item for item in items if item['Path'] == name), None)

    def _arrived(self, key: str, sha256: str, dest: Path) -> Path:
        """Download *key* beside *dest*, re-hash, then move it in; a mismatch leaves nothing behind."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f'.{dest.name}.', suffix='.part', dir=dest.parent)
        os.close(fd)  # rclone writes the path itself
        partial = Path(name)
        try:
            self._run('copyto', self.at(key), str(partial))
            got = file_sha256(partial)
            if got != sha256:
                msg = f'{self.backend}:{key}: arrived hashing {got[:16]}, the sha256 asked is {sha256[:16]}; discarded'
                raise ArrivalMismatch(msg)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
        return dest

    def put(self, path: Path) -> Ref:
        """Upload *path* unless an object of its sha256 and size is there, then re-hash what landed; its Ref.

        Raises:
            UploadUnverified: the stored bytes do not hash to *path*'s sha256; the object is removed first.

        """
        sha256 = file_sha256(path)
        ref = Ref(self.backend, self.key(sha256), path.stat().st_size, sha256)
        there = self._listed(ref.ref)
        if there is not None and int(there['Size']) == ref.bytes:
            return ref
        self._run('copyto', '--immutable', str(path), self.at(ref.ref))
        try:
            self.verify(ref)
        except ArrivalMismatch as cause:
            self._run('deletefile', self.at(ref.ref))
            msg = f'{self.backend}:{ref.ref}: the upload of {path.name} re-hashed otherwise ({cause}); removed'
            raise UploadUnverified(msg) from cause
        return ref

    def verify(self, ref: Ref) -> None:
        """Re-hash the stored bytes of *ref*; other bytes raise :class:`ArrivalMismatch`."""
        with tempfile.TemporaryDirectory(prefix='lab-commons-objects-') as scratch:
            self._arrived(ref.ref, ref.sha256, Path(scratch) / 'object')

    def get(self, ref: Ref, dest: Path) -> Path:
        """Download *ref*'s bytes to the file *dest*, re-hashed: a mismatch leaves no file and is a miss."""
        return self._arrived(ref.ref, ref.sha256, dest)

    def fetch(self, sha256: str, dest: Path, *, lock: Collection[str]) -> Path:
        """Download the object a lock names by *sha256* to the file *dest*, re-hashed.

        Raises:
            NotLocked: *lock* (the sha256s the caller's lock names) does not name *sha256*; the remote is not asked.
            ArrivalMismatch: the bytes that arrived do not hash to *sha256*; no file is left.

        """
        key = self.key(sha256)
        if sha256 not in lock:
            msg = f'{sha256[:16]} is not named by the lock ({len(lock)} object(s)): only lock-named objects are read'
            raise NotLocked(msg)
        return self._arrived(key, sha256, dest)


def declared_objects(
    declared: _storage.Storage | None = None, *, rclone: Sequence[str] | None = None, config: Path | None = None
) -> RcloneObjects | None:
    """The family's run store (``[storage] runs``); ``None`` when undeclared, with nothing resolved.

    A product passes nothing: the pinned :func:`rclone_exe` and the rendered :data:`RCLONE_CONFIG` are resolved
    here. dev-lab's reach door passes the *rclone* it names and the *config* it just rendered (its ``--to``).
    Reach is not proven here: ``python -m dev_lab.dev.storage check <remote>`` lists the remote.

    Raises:
        RcloneAbsent: no *rclone* given and the pinned one is not placed.
        ConfigUnrendered: no *config* given and :data:`RCLONE_CONFIG` was never rendered.

    """
    declared = _storage.declared() if declared is None else declared
    if declared.runs is None:
        return None
    argv = (str(rclone_exe()),) if rclone is None else tuple(rclone)
    if config is None:
        config = RCLONE_CONFIG.expanduser()
        if not config.is_file():
            msg = (
                f'no rendered rclone config at {config}: `python -m dev_lab.dev.storage render` '
                f'(then `python -m dev_lab.dev.storage check {declared.runs_remote}`)'
            )
            raise ConfigUnrendered(msg)
    return RcloneObjects(declared.runs, OBJECTS_PREFIX, (*argv, f'--config={config.expanduser()}'))


# --- PACK AND UNPACK (v1 ruling D125 F7) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One member of a pack: its relative POSIX *name*, the *sha256* of its content and its *size* in bytes."""

    name: str
    sha256: str
    size: int

    @property
    def entry(self) -> str:
        """The zip entry holding the content: ``objects/<sha[:2]>/<sha>``, shared by every member of that content."""
        return f'{PACK_OBJECTS}/{self.sha256[:2]}/{self.sha256}'


def _safe_name(name: str) -> str:
    parts = name.split('/')
    if not name or name.startswith('/') or ':' in name or '\\' in name or any(p in {'', '.', '..'} for p in parts):
        msg = f'pack member {name!r} is not a relative path inside the pack (POSIX, no "..", no drive)'
        raise PackRefused(msg)
    return name


def _manifest_text(members: Sequence[Member], meta: Mapping[str, str]) -> str:
    blocks = [f'[pack]\nformat = {PACK_FORMAT}\n']
    if meta:
        blocks.append('[meta]\n' + ''.join(f'{json.dumps(k)} = {json.dumps(v)}\n' for k, v in sorted(meta.items())))
    blocks += [f'[[member]]\nname = {json.dumps(m.name)}\nsha256 = "{m.sha256}"\nsize = {m.size}\n' for m in members]
    return '\n'.join(blocks)


def pack(files: Mapping[str, Path], archive: Path, *, meta: Mapping[str, str] | None = None) -> Path:
    """Zip *files* (member name -> source path) into *archive* with :data:`MANIFEST`; each content stored once.

    Raises:
        PackRefused: a member name is not a relative path inside the pack.

    """
    members = sorted(
        (Member(_safe_name(name), file_sha256(path), path.stat().st_size) for name, path in files.items()),
        key=lambda m: m.name,
    )
    sources = {m.entry: files[m.name] for m in members}
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr(MANIFEST, _manifest_text(members, meta or {}))
        for entry, source in sorted(sources.items()):
            book.write(source, entry)
    return archive


def read_manifest(archive: Path) -> tuple[Member, ...]:
    """The members *archive*'s manifest names, every name checked safe.

    Raises:
        PackRefused: no manifest, an unknown format, or a member name that escapes the pack.

    """
    with zipfile.ZipFile(archive) as book:
        return _members(book, archive)


def _members(book: zipfile.ZipFile, archive: Path) -> tuple[Member, ...]:
    try:
        manifest = tomllib.loads(book.read(MANIFEST).decode('utf-8'))
    except KeyError:
        msg = f'{archive.name} holds no {MANIFEST}: it is not a pack'
        raise PackRefused(msg) from None
    found = manifest.get('pack', {}).get('format')
    if found != PACK_FORMAT:
        msg = f'{archive.name} is pack format {found!r}; this reader reads format {PACK_FORMAT}'
        raise PackRefused(msg)
    return tuple(
        Member(_safe_name(str(row['name'])), str(row['sha256']), int(row['size'])) for row in manifest.get('member', [])
    )


def _verified(book: zipfile.ZipFile, archive: Path, members: Sequence[Member]) -> dict[str, bytes]:
    named = {MANIFEST} | {m.entry for m in members}
    for entry in book.namelist():
        if entry not in named:
            msg = f'{archive.name}: its manifest names no {entry!r}'
            raise PackRefused(msg)
    contents: dict[str, bytes] = {}
    for member in members:
        if member.entry not in contents:
            try:
                contents[member.entry] = book.read(member.entry)
            except KeyError:
                msg = f'{archive.name}: {member.name} has no content entry {member.entry}'
                raise PackRefused(msg) from None
        data = contents[member.entry]
        if hashlib.sha256(data).hexdigest() != member.sha256 or len(data) != member.size:
            msg = f'{archive.name}: {member.name} does not hash to its manifest ({member.sha256[:16]})'
            raise PackRefused(msg)
    return contents


def unpack(archive: Path, dest: Path) -> dict[str, Path]:
    """Every member of *archive* written under *dest*, each re-hashed first; member name -> written path.

    Nothing is written unless the whole pack verifies.

    Raises:
        PackRefused: a member name escapes *dest*, the zip holds an entry the manifest does not name or lacks one it
            does, or a member's bytes do not hash to the manifest.

    """
    with zipfile.ZipFile(archive) as book:
        members = _members(book, archive)
        contents = _verified(book, archive, members)
    written = {}
    for member in members:
        target = dest.joinpath(*member.name.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[member.entry])
        written[member.name] = target
    return written
