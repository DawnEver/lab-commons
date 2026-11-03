"""THE MACHINE HOME: one root per machine for everything the toolchain keeps outside a repo (user ruling 2026-11-03).

``~/.motronics/`` (or ``$MOTRONICS_HOME``) holds the toolchain's per-machine facts, in fixed parts (:data:`PARTS`):

* ``config/motronics.toml`` -- THE per-machine file (:mod:`lab_commons.config` reads it);
* ``state/`` -- what must outlive any one checkout (forge runs, alarms, ledgers, learned asks);
* ``cache/`` -- disposable;
* ``tools/`` -- external tools the toolchain installs;
* ``repos/`` -- the checkouts a serving host clones;
* ``backups/`` -- copies of the machine file taken before an edit.

THE REPOS TABLE. ``[repos]`` of the machine file maps a repo's name to its ABSOLUTE checkout path on this
machine -- the one place a checkout's location is declared (a workstation keeps its repos where the user
works; a serving host under ``repos/``). A repo's ``.claude/worktrees/<name>`` is that repo too.

THE WRITE ROOTS derive from it (:func:`write_roots`): the home, and each configured repo's ``output/`` and
``scratch/`` -- nothing else is ever written (:func:`allowed`). Temporary files are OUTPUT: a process
working in a repo has ``TMP``/``TEMP``/``TMPDIR`` at that repo's ``output/tmp/`` (:func:`temp_environment`);
a writer with no configured repo is refused, never given a fallback directory.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Final

__all__ = [
    'HOME_ENV',
    'PARTS',
    'TEMP_NAMES',
    'allowed',
    'config_path',
    'home',
    'part',
    'repo_of',
    'repos',
    'temp_environment',
    'temp_of',
    'write_roots',
]

#: Environment override of the machine home.
HOME_ENV: Final = 'MOTRONICS_HOME'

#: The fixed parts of the home, each a directory directly under it.
PARTS: Final = ('config', 'state', 'cache', 'tools', 'repos', 'backups')

#: Every name a platform reads its temporary directory from.
TEMP_NAMES: Final = ('TMP', 'TEMP', 'TMPDIR')

#: A repo's two writable children.
_WRITABLE: Final = ('output', 'scratch')

#: Where a repo's worktrees live inside it: a worktree is the repo.
_WORKTREES: Final = ('.claude', 'worktrees')


def home() -> Path:
    """``$MOTRONICS_HOME``, else ``~/.motronics``."""
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / '.motronics'


def part(name: str) -> Path:
    """The directory of one fixed part of the home; an unknown name is refused."""
    if name not in PARTS:
        msg = f'{name!r} is not a part of the machine home; the parts are {list(PARTS)}'
        raise ValueError(msg)
    return home() / name


def config_path() -> Path:
    """The one per-machine file, ``<home>/config/motronics.toml``."""
    return part('config') / 'motronics.toml'


def repos() -> dict[str, Path]:
    """The ``[repos]`` table: each repo's absolute checkout path on this machine; ``{}`` when undeclared."""
    path = config_path()
    table = tomllib.loads(path.read_text(encoding='utf-8')).get('repos', {}) if path.is_file() else {}
    if not isinstance(table, dict):
        msg = f'{path}: [repos] must be a table of name = "<absolute path>"'
        raise TypeError(msg)
    found: dict[str, Path] = {}
    for name, value in table.items():
        where = Path(str(value)).expanduser()
        if not isinstance(value, str) or not where.is_absolute():
            msg = f'{path}: [repos] {name} = {value!r} must be an absolute path'
            raise ValueError(msg)
        found[name] = where
    return found


def write_roots() -> tuple[Path, ...]:
    """The home, then each configured repo's ``output/`` and ``scratch/``."""
    return (home(), *(root / child for root in repos().values() for child in _WRITABLE))


def _resolved(path: Path) -> Path:
    return Path(os.path.normpath(Path(path).expanduser().absolute()))


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def repo_of(path: Path) -> Path | None:
    """The configured repo -- or that repo's worktree -- *path* lies in; ``None`` outside every one."""
    target = _resolved(path)
    for root in sorted((_resolved(r) for r in repos().values()), key=lambda r: len(r.parts), reverse=True):
        if not _within(target, root):
            continue
        rest = target.relative_to(root).parts
        if rest[: len(_WORKTREES)] == _WORKTREES and len(rest) > len(_WORKTREES):
            return root.joinpath(*_WORKTREES, rest[len(_WORKTREES)])
        return root
    return None


def allowed(path: Path) -> bool:
    """Whether *path* lies in the home or in a configured repo's (or its worktree's) ``output/`` or ``scratch/``."""
    target = _resolved(path)
    if _within(target, _resolved(home())):
        return True
    repo = repo_of(target)
    return repo is not None and any(_within(target, repo / child) for child in _WRITABLE)


def temp_of(path: Path) -> Path:
    """The temporary directory of the repo *path* works in: its ``output/tmp/``. No repo is refused."""
    repo = repo_of(path)
    if repo is None:
        msg = f'{path} is in no configured repo ([repos] of {config_path()}): a writer needs a repo context'
        raise ValueError(msg)
    return repo / 'output' / 'tmp'


def temp_environment(path: Path) -> dict[str, str]:
    """Every :data:`TEMP_NAMES` pointed at :func:`temp_of` *path*, the directory created."""
    temp = temp_of(path)
    temp.mkdir(parents=True, exist_ok=True)
    return dict.fromkeys(TEMP_NAMES, str(temp))
