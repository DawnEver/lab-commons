"""WHERE a repo's bulky state lives on THIS machine: one resolver over a fixed set of kinds.

A checkout writes run output, virtual environments, worktrees, caches and build trees. By default each
lives in the checkout (:data:`KINDS`); a machine whose disk is small binds a kind to another root in
its machine file, and every consumer reaches that kind ONLY through :func:`root` -- so the binding is
one fact, and a cleaner, a guard and a writer all agree where the kind is.

The machine file (:mod:`lab_commons.config`) may declare::

    [storage]
    roots = { output = "E:/work/{repo}/output", venvs = "E:/work/{repo}/venvs" }
    free_floor = "50 GiB"               # the free space every regenerable cache leaves on its volume
    quota = { probe = ["mmlsquota", "--block-size", "1"], format = "mmlsquota" }  # only where a quota binds
    free_floor_files = 20000            # only with a file-limited quota (default: 1% of its file limit)

    [storage.reach.results]             # one sub-table per family remote this machine reaches
    credentials_file = "~/.motronics/secrets/results"   # that remote's secret, placed by `ops secret place`

and the FAMILY file declares the remotes every machine shares, once and without a secret::

    [storage.remotes.results]
    type = "s3"
    endpoint = "https://objects.example"
    access_key_id = "<the key's ID>"     # a fact: declared, never asked
    secret_keys = ["secret_access_key"]  # the only keys the credentials_file holds and a person is asked

    [storage]
    runs = "results:bucket"              # the remote:path every run's oversize result files go to (optional)

``{repo}`` in a root is the repo's name. A bound root is absolute. Layer placement is refused by
name: ``roots``, ``reach`` and the floor keys belong to one machine, ``remotes`` to the family; a machine file that
declares no family layer is a render of family + host and carries the family's ``remotes`` inline. A cache is
bounded by ``free_floor`` (a size, :mod:`lab_commons.quantity`; default :data:`FREE_FLOOR_DEFAULT`; the door is
:mod:`lab_commons.compute.floor`),
never a fixed size. Where an account QUOTA binds below the volume (a shared filesystem), ``quota`` names its probe
and format (:mod:`lab_commons.headroom`) and the floor holds against the smaller of the two. A kind outside
:data:`KINDS`, a remote the family does not declare, and a reached remote with no ``credentials_file`` are
refused by name; so are the retired spellings -- a ``secrets`` directory, a list-form ``reach`` (user ruling
2026-11-19: one declared file per secret, the family's ``credentials_file`` key), a fixed cap key (D54) and the
unit-suffixed ``free_floor_gb`` (user ruling 2026-11-25: the value carries its unit; refused with its new line).
A remote's options are handed to the transport (rclone) verbatim; this module names
no provider and no host. The family's ``runs`` names the store every run's oversize files use (``<remote>:<path>``,
the remote one the family declares); undeclared, no run has one and an oversize result stays local
(:class:`lab_commons.compute.store.TooLarge`). Whether THIS machine reaches it is the transport's door to prove.

This module never moves data: a binding takes effect for new writes, and moving an existing tree is
the migrate door's (``dev_lab.dev.storage migrate``) -- once, never an upgrade on read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.headroom import FORMATS, Floor, Quota
from lab_commons.quantity import QuantityRefused, Renamed, parse, refuse_renamed

__all__ = [
    'FREE_FLOOR_DEFAULT',
    'KINDS',
    'TABLE',
    'Remote',
    'Storage',
    'StorageError',
    'declared',
    'free_floor',
    'root',
]

#: The table both layers use.
TABLE: Final = 'storage'

GIB: Final = 1024**3
#: The free space (bytes) every regenerable cache leaves on its volume when the machine declares none (D54): 50 GiB.
FREE_FLOOR_DEFAULT: Final = 50 * GIB

#: Every kind, with its in-tree default relative to the checkout (``None``: the tool's own default,
#: outside the checkout -- a package cache).
KINDS: Final[Mapping[str, str | None]] = {
    'output': 'output',
    'venvs': '.venvs',
    'worktrees': '.claude/worktrees',
    'vendor_cache': 'output/vendor_cache',
    'build': 'target',
    'temp': 'output/temp',
    'uv_cache': None,
}

_MACHINE_KEYS: Final = frozenset({'roots', 'reach', 'free_floor', 'free_floor_files', 'quota'})
#: Unit-suffixed keys retired by the 2026-11-25 ruling, refused with the line that replaces them; never read.
_RENAMED: Final = {'free_floor_gb': Renamed('free_floor', 'GiB')}

#: Retired machine spellings (2026-11-19), refused by name with the shape that replaced them; never read.
_RETIRED_SHAPE: Final = (
    '[storage.reach.<remote>] credentials_file = "<file>" -- one declared secret file per reached remote'
)
#: Fixed cache caps, retired by D54 for the free-space floor; refused by name, never read.
_CAP_REMEDY: Final = (
    'a cache is bounded by the free space its volume keeps, not a fixed size (D54) -- declare [storage] '
    'free_floor in the machine file (default "50 GiB")'
)
_RETIRED_KEYS: Final = {
    'secrets': f'a secrets directory is retired: declare {_RETIRED_SHAPE}',
    'vendor_cache_cap_gb': _CAP_REMEDY,
    'cache_cap_gb': _CAP_REMEDY,
    'cap_gb': _CAP_REMEDY,
}
_FAMILY_KEYS: Final = frozenset({'remotes', 'runs'})


class StorageError(ValueError):
    """A ``[storage]`` declaration refused: the message names the key and its remedy."""


@dataclass(frozen=True)
class Remote:
    """One reached family remote: its name, the transport options handed over verbatim, its secret's file.

    ``credentials_file`` is ``None`` only for a family remote no machine reaches here.
    """

    name: str
    options: Mapping[str, str]
    credentials_file: Path | None = None
    #: What each secret key is and where a person finds it (``[storage.remotes.<r>.ask]``): shown when it is asked.
    ask: Mapping[str, str] = field(default_factory=dict)
    #: The regular expression each secret key's value must match (``[storage.remotes.<r>.shape]``), checked at entry.
    shape: Mapping[str, str] = field(default_factory=dict)
    #: The keys the ``credentials_file`` holds (``secret_keys``): the only values a person is asked. Every other
    #: transport key -- an S3 access key ID too -- is a declared fact among :attr:`options`, never asked or placed.
    secret_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Storage:
    """One machine's resolved ``[storage]``: its bound roots and the remotes it reaches, each with its secret file."""

    roots: Mapping[str, str] = field(default_factory=dict)
    reach: tuple[Remote, ...] = ()
    free_floor: int = FREE_FLOOR_DEFAULT  # bytes
    free_floor_files: int | None = None
    quota: Quota | None = None
    #: The family's ``runs``: the ``<remote>:<path>`` every run's oversize files go to; ``None``: undeclared.
    runs: str | None = None
    #: The remote :attr:`runs` names, as the family declares it, whether or not this machine reaches it.
    runs_remote: str | None = None


def free_floor(storage: Storage | None = None) -> Floor:
    """What every regenerable cache on this machine leaves free -- bytes, files, and the quota they hold in (D54)."""
    held = storage if storage is not None else declared()
    return Floor(held.free_floor, held.free_floor_files, held.quota)


def _table(layer: Mapping[str, Any], where: str) -> dict[str, Any]:
    value = layer.get(TABLE, {})
    if not isinstance(value, dict):
        msg = f'{where}: [{TABLE}] must be a table, got {type(value).__name__}'
        raise StorageError(msg)
    return value


def declared(machine: Mapping[str, Any] | None = None, family: Mapping[str, Any] | None = None) -> Storage:
    """Read and check ``[storage]`` from the machine file and the family file (both read when ``None``)."""
    if machine is None or family is None:
        from lab_commons import config  # noqa: PLC0415 -- config reads the files; tests pass layers

        machine = config.machine() if machine is None else machine
        family = config.family() if family is None else family
    local = _table(machine, 'the machine file')
    shared = _table(family, 'the family file')
    if not family:
        # No family layer: the machine file is a RENDER of family + host and carries the family's tables inline,
        # so its remotes ARE the family's -- read once from there, never a restatement.
        shared = {key: local[key] for key in _FAMILY_KEYS & set(local)}
        local = {key: value for key, value in local.items() if key not in _FAMILY_KEYS}
    _check_layers(local, shared)
    roots = _roots(local.get('roots', {}))
    reach = _reach(local.get('reach', {}), shared.get('remotes', {}))
    runs = _runs(shared.get('runs'), shared.get('remotes', {}))
    return Storage(
        roots, reach, _floor(local), _files(local), _quota(local.get('quota')), runs,
        runs.partition(':')[0] if runs else None,
    )  # fmt: skip


def _runs(value: object, remotes: Mapping[str, Any]) -> str | None:
    """The family's ``runs``: ``<remote>:<path>`` on a remote it declares, or ``None`` undeclared."""
    if value is None:
        return None
    remote, colon, path = value.partition(':') if isinstance(value, str) else ('', '', '')
    if not (remote and colon and path.strip('/')):
        msg = f'the family file: [{TABLE}] runs = {value!r} must be "<remote>:<path>", e.g. "results:bucket"'
        raise StorageError(msg)
    if remote not in remotes:
        msg = (
            f'the family file: [{TABLE}] runs names remote {remote!r}, which it declares no '
            f'[{TABLE}.remotes.{remote}] for; declared: {sorted(remotes)}'
        )
        raise StorageError(msg)
    return value


def _files(local: Mapping[str, Any]) -> int | None:
    """The machine's ``free_floor_files``: a positive count, or ``None`` (1% of a quota's file limit)."""
    value = local.get('free_floor_files')
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        msg = f'the machine file: [{TABLE}] free_floor_files = {value!r} must be a positive count of files'
        raise StorageError(msg)
    return value


def _quota(value: object) -> Quota | None:
    """The machine's ``quota``: a probe argv and one of :data:`lab_commons.headroom.FORMATS`."""
    if value is None:
        return None
    probe = value.get('probe') if isinstance(value, dict) else None
    fmt = value.get('format') if isinstance(value, dict) else None
    if not (isinstance(probe, list) and probe and all(isinstance(one, str) and one for one in probe)):
        msg = f'the machine file: [{TABLE}] quota.probe must be a non-empty argv list of strings, got {probe!r}'
        raise StorageError(msg)
    if fmt not in FORMATS:
        msg = f'the machine file: [{TABLE}] quota.format = {fmt!r} must be one of {sorted(FORMATS)}'
        raise StorageError(msg)
    if unknown := sorted(set(value) - {'probe', 'format'}):
        msg = f'the machine file: [{TABLE}] quota.{unknown[0]} is not a quota key; keys: probe, format'
        raise StorageError(msg)
    return Quota(tuple(probe), fmt)


def _floor(local: Mapping[str, Any]) -> int:
    """The machine's ``free_floor`` in bytes: a positive size, :data:`FREE_FLOOR_DEFAULT` when undeclared."""
    if 'free_floor' not in local:
        return FREE_FLOOR_DEFAULT
    try:
        return int(parse(f'the machine file: [{TABLE}] free_floor', local['free_floor'], 'size', positive=True))
    except QuantityRefused as bad:
        raise StorageError(str(bad)) from bad


def _check_layers(local: Mapping[str, Any], shared: Mapping[str, Any]) -> None:
    """Refuse a retired key, a key in the wrong layer, or no storage key at all."""
    try:
        refuse_renamed(f'the machine file: [{TABLE}]', local, _RENAMED)
        refuse_renamed(f'the family file: [{TABLE}]', shared, _RENAMED)
    except QuantityRefused as retired:
        raise StorageError(str(retired)) from retired
    for key, remedy in _RETIRED_KEYS.items():
        if key in local or key in shared:
            msg = f'[{TABLE}] {key} is retired: {remedy}'
            raise StorageError(msg)
    if misplaced := sorted(set(local) & _FAMILY_KEYS):
        msg = f'the machine file: [{TABLE}] {misplaced[0]} is a family fact: declare it once in the family file'
        raise StorageError(msg)
    if misplaced := sorted(set(shared) & _MACHINE_KEYS):
        msg = f'the family file: [{TABLE}] {misplaced[0]} belongs to one machine: move it to each machine file'
        raise StorageError(msg)
    for where, table, allowed in (
        ('the machine file', local, _MACHINE_KEYS),
        ('the family file', shared, _FAMILY_KEYS),
    ):
        if unknown := sorted(set(table) - allowed):
            msg = f'{where}: [{TABLE}] {unknown[0]} is not a storage key; allowed: {sorted(allowed)}'
            raise StorageError(msg)


def _roots(roots: Mapping[str, Any]) -> dict[str, str]:
    """The bound roots: each a known kind and an absolute path."""
    if unknown := sorted(set(roots) - set(KINDS)):
        msg = f'the machine file: [{TABLE}.roots] {unknown[0]} is not a kind; kinds: {sorted(KINDS)}'
        raise StorageError(msg)
    for kind, value in roots.items():
        if not Path(str(value).replace('{repo}', 'r')).expanduser().is_absolute():
            msg = f'the machine file: [{TABLE}.roots] {kind} = {value!r} is relative: bind an absolute root'
            raise StorageError(msg)
    return {str(kind): str(value) for kind, value in roots.items()}


#: A family remote's keys that are not rclone options: the family-wide reach, its secret keys, their hints and shapes.
_REMOTE_META: Final = frozenset({'reach', 'secret_keys', 'ask', 'shape'})


def _secret_keys(name: str, value: object, options: Mapping[str, str], asked: Mapping[str, str]) -> tuple[str, ...]:
    """A reached remote's declared ``secret_keys``: present, disjoint from its facts, and the only keys asked."""
    where = f'the family file: [{TABLE}.remotes.{name}]'
    if not isinstance(value, list) or not value or not all(isinstance(k, str) and k for k in value):
        msg = f'{where} declares no secret_keys: list the keys its credentials_file holds, e.g. ["secret_access_key"]'
        raise StorageError(msg)
    if both := sorted(set(value) & set(options)):
        msg = f'{where} declares {both[0]} both as a fact and as a secret key: a fact is declared, a secret placed'
        raise StorageError(msg)
    if not_secret := sorted(set(asked) - set(value)):
        msg = (
            f'{where} ask/shape names {not_secret[0]}, which is not a secret key: only a secret is asked; '
            'declare a fact among the remote options'
        )
        raise StorageError(msg)
    return tuple(value)


def _reach(reach: object, remotes: Mapping[str, Any]) -> tuple[Remote, ...]:
    """The family remotes this machine reaches, each with its credentials file.

    A family remote declaring ``[storage.remotes.<r>.reach] credentials_file`` is reached by EVERY machine at that
    file -- the fact stated once (user ruling 2026-11-20: every machine holds the key). A machine file names a remote
    in ``[storage.reach]`` only to reach one the family does not give everyone, or to keep the key elsewhere; the
    same file restated is refused (one source).
    """
    if not isinstance(reach, dict):
        msg = f'the machine file: [{TABLE}] reach as a list is retired: declare {_RETIRED_SHAPE}'
        raise StorageError(msg)
    everyone = {
        name: dict(remote['reach'])
        for name, remote in remotes.items()
        if isinstance(remote, dict) and isinstance(remote.get('reach'), dict)
    }
    for name, entry in reach.items():
        if name in everyone and isinstance(entry, dict) and entry == everyone[name]:
            msg = (
                f'the machine file: [{TABLE}.reach.{name}] restates the family reach of {name}: delete it here '
                '(the family file gives every machine that credentials_file)'
            )
            raise StorageError(msg)
    found = []
    for name, entry in {**everyone, **reach}.items():
        if name not in remotes:
            msg = f'the machine file: [{TABLE}.reach] names {name!r}, which the family file declares no remote for'
            raise StorageError(msg)
        file = entry.get('credentials_file') if isinstance(entry, dict) else None
        if not file:
            msg = (
                f'the machine file: [{TABLE}.reach.{name}] declares no credentials_file: declare the file its '
                'secret lives in, then place it (ops secret place --path <that file>)'
            )
            raise StorageError(msg)
        if unknown := sorted(set(entry) - {'credentials_file'}):
            msg = f'the machine file: [{TABLE}.reach.{name}] {unknown[0]} is not a reach key; allowed: credentials_file'
            raise StorageError(msg)
        options = {str(k): str(v) for k, v in remotes[name].items() if k not in _REMOTE_META}
        ask = {str(k): str(v) for k, v in dict(remotes[name].get('ask', {})).items()}
        shape = {str(k): str(v) for k, v in dict(remotes[name].get('shape', {})).items()}
        secrets = _secret_keys(str(name), remotes[name].get('secret_keys'), options, {**ask, **shape})
        found.append(Remote(str(name), options, Path(str(file)).expanduser(), ask, shape, secrets))
    return tuple(found)


def root(kind: str, repo: str, tree: Path, *, storage: Storage | None = None) -> Path | None:
    """Where *kind* of *repo* (checked out at *tree*) lives on this machine.

    A bound kind is its machine root with ``{repo}`` filled; an unbound one is its in-tree default under
    *tree* (``None`` for a kind whose default is the tool's own, e.g. ``uv_cache``).
    """
    if kind not in KINDS:
        msg = f'{kind!r} is not a storage kind; kinds: {sorted(KINDS)}'
        raise StorageError(msg)
    bound = (storage if storage is not None else declared()).roots.get(kind)
    if bound is not None:
        return Path(bound.replace('{repo}', repo)).expanduser()
    default = KINDS[kind]
    return None if default is None else tree / default
