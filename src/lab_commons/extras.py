"""ONE SELECTION OF EXTRAS PER NEED-SET -- every environment a repo builds derives its extras here, never a copy.

A repo declares ONCE, in its pyproject, the extras every environment of it carries and which extra serves a need
(the need names are its tests' resource markers, :mod:`lab_commons.hpc.needsets`)::

    [tool.dev_lab.dep]
    extras = ["dev"]                       # every environment's extras
    needs = { learn = "learn" }            # need -> the extra that serves it (a heavy one stays selectable)
    no_install = ["<pkg>"]                 # workspace packages no sync installs (built by their own step)

A CI part, a cluster build (:mod:`lab_commons.hpc.builds`), a research job (``[job] needs``) and a workstation
sync (``dev_lab.dev.dep``) all ask :func:`extras_for` / :func:`sync_command` for THEIR need-set, so a part that
needs nothing heavy installs nothing heavy (user, 2026-11-20: "most tasks don't need torch").

NO "EVERYTHING" SWITCH: there is no ``--all-extras`` path. A repo with optional dependencies and no declared
``extras`` is refused by name (an undeclared default is the over-install this module exists to end); a need
mapped to an extra the repo does not declare is refused by name, and so is any key the table does not know.
A ``no_install`` package (e.g. a native module its own build step produces) becomes ``--no-install-package``
on every sync. Standard library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = ['TABLE', 'Declared', 'declared', 'extras_for', 'sync_command']

#: Where a repo declares its extras: ``[tool.dev_lab.dep]``.
TABLE: Final = 'tool.dev_lab.dep'
#: What a shared (cross-sha) environment adds, so it holds dependencies only (:mod:`lab_commons.hpc.builds`).
NO_PROJECT: Final = '--no-install-project'
#: The keys ``[tool.dev_lab.dep]`` holds; any other is refused by name.
KEYS: Final = frozenset({'extras', 'needs', 'no_install'})


@dataclass(frozen=True)
class Declared:
    """A repo's declaration: the extras every environment carries, and the extra each need maps to."""

    extras: tuple[str, ...]
    needs: Mapping[str, str]
    no_install: tuple[str, ...] = ()


def declared(manifest: Mapping[str, Any], origin: str = 'pyproject.toml') -> Declared:
    """*manifest*'s (a parsed pyproject) declaration, refused by name when it is missing or names no extra."""
    available = manifest.get('project', {}).get('optional-dependencies', {})
    dep = manifest.get('tool', {}).get('dev_lab', {}).get('dep', {})
    if not isinstance(dep, dict):
        msg = f'{origin}: [{TABLE}] must be a table'
        raise TypeError(msg)
    if 'all' in dep:
        msg = f'{origin}: [{TABLE}] all is retired -- there is no install-everything switch; list the extras'
        raise ValueError(msg)
    if unknown_keys := sorted(set(dep) - KEYS):
        msg = f'{origin}: [{TABLE}] has unknown keys {unknown_keys} -- it holds only {sorted(KEYS)}'
        raise ValueError(msg)
    if available and 'extras' not in dep:
        msg = (
            f'{origin}: declares optional dependencies {sorted(available)} but no [{TABLE}] extras -- '
            'list the extras every environment carries (extras = [] for none) and map heavy ones under needs'
        )
        raise ValueError(msg)
    extras = dep.get('extras', [])
    needs = dep.get('needs', {})
    if not isinstance(extras, list) or not all(isinstance(e, str) for e in extras):
        msg = f'{origin}: [{TABLE}] extras must be a list of extra names'
        raise ValueError(msg)
    if not isinstance(needs, dict) or not all(isinstance(v, str) for v in needs.values()):
        msg = f'{origin}: [{TABLE}] needs must be a table of need = "<extra>"'
        raise ValueError(msg)
    no_install = dep.get('no_install', [])
    if not isinstance(no_install, list) or not all(isinstance(p, str) for p in no_install):
        msg = f'{origin}: [{TABLE}] no_install must be a list of package names'
        raise ValueError(msg)
    if unknown := sorted({*extras, *needs.values()} - set(available)):
        msg = f'{origin}: [{TABLE}] names {unknown}, no declared extra ({sorted(available)})'
        raise ValueError(msg)
    return Declared(tuple(extras), dict(needs), tuple(no_install))


def extras_for(manifest: Mapping[str, Any], needs: Iterable[str], origin: str = 'pyproject.toml') -> tuple[str, ...]:
    """The extras an environment for *needs* selects: the declared ones, then each need's extra, in order.

    A need the repo maps to no extra (an OS, an external tool) installs nothing.
    """
    found = declared(manifest, origin)
    return tuple(dict.fromkeys([*found.extras, *(found.needs[n] for n in needs if n in found.needs)]))


def sync_command(
    manifest: Mapping[str, Any], needs: Iterable[str] = (), *, project: bool = True, origin: str = 'pyproject.toml'
) -> str:
    """The ``uv sync`` building the environment for *needs*; ``project=False`` installs dependencies only."""
    skip = (f'--no-install-package {p}' for p in declared(manifest, origin).no_install)
    words = ['uv', 'sync', '--frozen', *(f'--extra {e}' for e in extras_for(manifest, needs, origin)), *skip]
    return ' '.join(words if project else [*words, NO_PROJECT])
