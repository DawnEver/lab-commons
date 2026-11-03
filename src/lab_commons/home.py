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

THE TOOLS TABLE (:func:`tools_table`). ``[tools]`` has exactly two keys: ``install_root`` (the absolute directory
the user confirmed an external tool may be installed into, e.g. ``<X>:/Program Files/``; it joins the write roots)
and ``min_free_gb`` (the free space an install needs there). Any other key is refused by name. The toolchain's
own managed tool root is :func:`tools`.

OUTSIDE THE POLICY (:func:`exempt`): Claude's own configuration (:data:`CLAUDE_HOMES` under the user's home --
never touched by the toolchain) and the AGENT repos of ``[agent_repos]`` (name -> absolute path; Claude Code's
configuration and plugin market), which are not toolchain repos.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    'CLAUDE_HOMES',
    'HOME_ENV',
    'PARTS',
    'TEMP_NAMES',
    'Tools',
    'agent_repos',
    'allowed',
    'config_path',
    'exempt',
    'home',
    'part',
    'repo_of',
    'repos',
    'temp_environment',
    'temp_of',
    'tools',
    'tools_table',
    'write_roots',
]

#: Environment override of the machine home.
HOME_ENV: Final = 'MOTRONICS_HOME'

#: The fixed parts of the home, each a directory directly under it.
PARTS: Final = ('config', 'state', 'cache', 'tools', 'repos', 'backups')

#: Every name a platform reads its temporary directory from.
TEMP_NAMES: Final = ('TMP', 'TEMP', 'TMPDIR')

#: Claude's configuration directories under the user's home: outside the policy (user ruling 2026-11-03).
CLAUDE_HOMES: Final = ('.claude', '.claude-evans')

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


def tools() -> Path:
    """The toolchain's managed tool root, ``<home>/tools``."""
    return part('tools')


def config_path() -> Path:
    """The one per-machine file, ``<home>/config/motronics.toml``."""
    return part('config') / 'motronics.toml'


def repos() -> dict[str, Path]:
    """The ``[repos]`` table: each repo's absolute checkout path on this machine; ``{}`` when undeclared."""
    return _paths('repos')


def agent_repos() -> dict[str, Path]:
    """The ``[agent_repos]`` table: the agent repos outside this policy; ``{}`` when undeclared."""
    return _paths('agent_repos')


def _paths(table_name: str) -> dict[str, Path]:
    path, table = config_path(), _table(table_name)
    if not isinstance(table, dict):
        msg = f'{path}: [{table_name}] must be a table of name = "<absolute path>"'
        raise TypeError(msg)
    found: dict[str, Path] = {}
    for name, value in table.items():
        where = Path(str(value)).expanduser()
        if not isinstance(value, str) or not where.is_absolute():
            msg = f'{path}: [{table_name}] {name} = {value!r} must be an absolute path'
            raise ValueError(msg)
        found[name] = where
    return found


@dataclass(frozen=True)
class Tools:
    """The ``[tools]`` table: where the user lets an external tool install, and the free space it needs."""

    install_root: Path | None
    min_free_gb: float | None


_TOOLS_KEYS: Final = frozenset({'install_root', 'min_free_gb'})


def _table(name: str) -> object:
    path = config_path()
    return tomllib.loads(path.read_text(encoding='utf-8')).get(name, {}) if path.is_file() else {}


def tools_table() -> Tools:
    """The ``[tools]`` table, validated; an unknown key, a relative root or a non-number size is refused."""
    path, table = config_path(), _table('tools')
    if not isinstance(table, dict):
        msg = f'{path}: [tools] must be a table'
        raise TypeError(msg)
    if unknown := sorted(set(table) - _TOOLS_KEYS):
        msg = f'{path}: [tools] has unknown key(s) {unknown}; the keys are {sorted(_TOOLS_KEYS)}'
        raise ValueError(msg)
    root, size = table.get('install_root'), table.get('min_free_gb')
    if root is not None and (not isinstance(root, str) or not Path(root).expanduser().is_absolute()):
        msg = f'{path}: [tools] install_root = {root!r} must be an absolute path'
        raise ValueError(msg)
    if size is not None and (isinstance(size, bool) or not isinstance(size, int | float)):
        msg = f'{path}: [tools] min_free_gb = {size!r} must be a number'
        raise ValueError(msg)
    return Tools(
        install_root=None if root is None else Path(root).expanduser(),
        min_free_gb=None if size is None else float(size),
    )


def write_roots() -> tuple[Path, ...]:
    """The home, each configured repo's ``output/`` and ``scratch/``, then ``[tools] install_root`` if declared."""
    install = tools_table().install_root
    return (
        home(),
        *(root / child for root in repos().values() for child in _WRITABLE),
        *(() if install is None else (install,)),
    )


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
    install = tools_table().install_root
    if _within(target, _resolved(home())) or (install is not None and _within(target, _resolved(install))):
        return True
    repo = repo_of(target)
    return repo is not None and any(_within(target, repo / child) for child in _WRITABLE)


def exempt(path: Path) -> bool:
    """Whether *path* is outside the policy: Claude's configuration or an agent repo."""
    target = _resolved(path)
    roots = [Path.home() / name for name in CLAUDE_HOMES] + list(agent_repos().values())
    return any(_within(target, _resolved(root)) for root in roots)


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
