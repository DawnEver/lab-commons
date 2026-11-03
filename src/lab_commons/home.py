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

SYNCED FOLDERS (:func:`sync_roots`): ``[sync] roots`` lists the globs of the folders a sync client shares with a
live human session (OneDrive); a write there needs the user's consent for that write. Undeclared, the platform's
default applies (:func:`sync_defaults`).

PATHS ARE COMPARED AS THE FILESYSTEM SEES THEM (:func:`canonical`, :func:`within`): every link (symlink, junction)
is resolved first, so a link inside ``output/`` is judged by where it leads and Ada's ``/home/<u>`` is its
``/gpfs01/home/<u>``; and case is ignored where the filesystem ignores it (Windows, macOS).
"""

from __future__ import annotations

import glob
import os
import sys
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
    'canonical',
    'config_path',
    'exempt',
    'home',
    'part',
    'repo_of',
    'repos',
    'sync_defaults',
    'sync_roots',
    'temp_environment',
    'temp_of',
    'tools',
    'tools_table',
    'within',
    'within_text',
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

#: The platforms whose default filesystem ignores case (NTFS, APFS): there two spellings are one path.
_CASELESS: Final = frozenset({'win32', 'darwin'})

#: Each platform's sync-client folders when ``[sync] roots`` is undeclared; none on Linux.
_SYNC_DEFAULTS: Final = {
    'win32': ('~/OneDrive*',),
    'darwin': ('~/Library/CloudStorage/OneDrive-*',),
}
_SYNC_KEYS: Final = frozenset({'roots'})


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


def canonical(path: Path) -> Path:
    """*path* as the filesystem resolves it: absolute, every link (symlink, junction) followed, ``..`` folded."""
    return Path(os.path.realpath(Path(path).expanduser().absolute()))


def _key(text: str, platform: str) -> str:
    folded = text.replace('\\', '/') if platform == 'win32' else text
    return folded.casefold() if platform in _CASELESS else folded


def within_text(path: str, root: str, *, platform: str = sys.platform) -> bool:
    """Whether *path* is *root* or under it, as spelled; case is ignored where *platform*'s filesystem ignores it."""
    target, base = _key(path, platform), _key(root, platform).rstrip('/')
    return target == base or target.startswith(base + '/')


def within(path: Path, root: Path) -> bool:
    """Whether *path* is *root* or under it, both resolved as the filesystem sees them (:func:`canonical`)."""
    return within_text(str(canonical(path)), str(canonical(root)))


def repo_of(path: Path) -> Path | None:
    """The configured repo -- or that repo's worktree -- *path* lies in, resolved; ``None`` outside every one."""
    target = canonical(path)
    for root in sorted((canonical(r) for r in repos().values()), key=lambda r: len(r.parts), reverse=True):
        if not within_text(str(target), str(root)):
            continue
        rest = target.parts[len(root.parts) :]
        head = tuple(part.casefold() for part in rest[: len(_WORKTREES)])
        if head == _WORKTREES and len(rest) > len(_WORKTREES):
            return root.joinpath(*_WORKTREES, rest[len(_WORKTREES)])
        return root
    return None


def allowed(path: Path) -> bool:
    """Whether *path* lies in the home or in a configured repo's (or its worktree's) ``output/`` or ``scratch/``."""
    install = tools_table().install_root
    if within(path, home()) or (install is not None and within(path, install)):
        return True
    repo = repo_of(path)
    return repo is not None and any(within(path, repo / child) for child in _WRITABLE)


def exempt(path: Path) -> bool:
    """Whether *path* is outside the policy: Claude's configuration or an agent repo."""
    roots = [Path.home() / name for name in CLAUDE_HOMES] + list(agent_repos().values())
    return any(within(path, root) for root in roots)


def sync_defaults(platform: str = sys.platform) -> tuple[str, ...]:
    """The sync-client folder globs of *platform* when ``[sync] roots`` is undeclared; none on Linux."""
    return _SYNC_DEFAULTS.get(platform, ())


def sync_roots() -> tuple[Path, ...]:
    """The synced folders on this machine: ``[sync] roots`` globs, else :func:`sync_defaults`, each expanded."""
    path, table = config_path(), _table('sync')
    if not isinstance(table, dict):
        msg = f'{path}: [sync] must be a table'
        raise TypeError(msg)
    if unknown := sorted(set(table) - _SYNC_KEYS):
        msg = f'{path}: [sync] has unknown key(s) {unknown}; the keys are {sorted(_SYNC_KEYS)}'
        raise ValueError(msg)
    patterns = table.get('roots', sync_defaults())
    if not isinstance(patterns, list | tuple) or not all(isinstance(one, str) for one in patterns):
        msg = f'{path}: [sync] roots = {patterns!r} must be a list of path globs'
        raise ValueError(msg)
    hits = (Path(hit) for one in patterns for hit in sorted(glob.glob(str(Path(one).expanduser()))))  # noqa: PTH207
    return tuple(hit for hit in hits if hit.is_dir())


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
