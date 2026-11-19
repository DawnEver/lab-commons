"""WHERE a repo's bulky state lives on THIS machine: one resolver over a fixed set of kinds.

A checkout writes run output, virtual environments, worktrees, caches and build trees. By default each
lives in the checkout (:data:`KINDS`); a machine whose disk is small binds a kind to another root in
its machine file, and every consumer reaches that kind ONLY through :func:`root` -- so the binding is
one fact, and a cleaner, a guard and a writer all agree where the kind is.

The machine file (:mod:`lab_commons.config`) may declare::

    [storage]
    roots = { output = "E:/work/{repo}/output", venvs = "E:/work/{repo}/venvs" }

    [storage.reach.results]             # one sub-table per family remote this machine reaches
    credentials_file = "~/.motronics/secrets/results"   # that remote's secret, placed by `ops secret place`

and the FAMILY file declares the remotes every machine shares, once and without a secret::

    [storage.remotes.results]
    type = "s3"
    endpoint = "https://objects.example"

``{repo}`` in a root is the repo's name. A bound root is absolute. Layer placement is refused by
name: ``roots`` and ``reach`` belong to one machine, ``remotes`` to the family. A kind outside
:data:`KINDS`, a remote the family does not declare, and a reached remote with no ``credentials_file`` are
refused by name; so are the retired spellings -- a ``secrets`` directory and a list-form ``reach``
(user ruling 2026-11-19: one declared file per secret, the family's ``credentials_file`` key).
A remote's options are handed to the transport (rclone) verbatim; this module names
no provider and no host.

This module never moves data: a binding takes effect for new writes, and moving an existing tree is
the migrate door's (``dev_lab.dev.storage migrate``) -- once, never an upgrade on read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = ['KINDS', 'TABLE', 'Remote', 'Storage', 'StorageError', 'declared', 'root']

#: The table both layers use.
TABLE: Final = 'storage'

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

_MACHINE_KEYS: Final = frozenset({'roots', 'reach'})

#: Retired machine spellings (2026-11-19), refused by name with the shape that replaced them; never read.
_RETIRED_SHAPE: Final = (
    '[storage.reach.<remote>] credentials_file = "<file>" -- one declared secret file per reached remote'
)
_RETIRED_KEYS: Final = {'secrets': f'a secrets directory is retired: declare {_RETIRED_SHAPE}'}
_FAMILY_KEYS: Final = frozenset({'remotes'})


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


@dataclass(frozen=True)
class Storage:
    """One machine's resolved ``[storage]``: its bound roots and the remotes it reaches, each with its secret file."""

    roots: Mapping[str, str] = field(default_factory=dict)
    reach: tuple[Remote, ...] = ()


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
    _check_layers(local, shared)
    roots = _roots(local.get('roots', {}))
    reach = _reach(local.get('reach', {}), shared.get('remotes', {}))
    return Storage(roots, reach)


def _check_layers(local: Mapping[str, Any], shared: Mapping[str, Any]) -> None:
    """Refuse a retired key, a key in the wrong layer, or no storage key at all."""
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


def _reach(reach: object, remotes: Mapping[str, Any]) -> tuple[Remote, ...]:
    """The family remotes this machine reaches: each a family-declared remote with its own credentials file."""
    if not isinstance(reach, dict):
        msg = f'the machine file: [{TABLE}] reach as a list is retired: declare {_RETIRED_SHAPE}'
        raise StorageError(msg)
    found = []
    for name, entry in reach.items():
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
        options = {str(k): str(v) for k, v in remotes[name].items()}
        found.append(Remote(str(name), options, Path(str(file)).expanduser()))
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
