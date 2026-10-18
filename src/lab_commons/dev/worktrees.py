"""The LOCAL WORKTREE CLEANUP DOOR: ``python -m lab_commons.dev.worktrees [--root R] [--prune] [--archive]``.

User rulings 2026-10-04 (AUTO-MODE-RUNS-THE-DOORS): local cleanup is a checked door an agent may run
unattended, nothing here pushes or touches origin, and a worktree is removed ONLY when it is CLEAN --
nothing modified, staged or untracked (``git status --porcelain`` empty). Work is committed or cleared
by a human first; BY DEFAULT this door never moves work aside to make a tree removable.

THE INCIDENT IT ENCODES (a consumer, 2026-10-04): a cleanup loop ran ``mkdir -p "$D" && mv output "$D"``
and then ``; git worktree remove --force``. The archive helper failed, ``mv`` never ran, and the ``;``
let the removal delete five worktrees' ignored ``output/``. So:

* a registered worktree is PRUNED only when it is clean, its HEAD is contained in a remote-tracking
  branch, and every ignored path in it is REGENERABLE (:func:`regenerable`) -- an ignored
  ``output/`` is work, BLOCKS it and is listed. Removal is ``git worktree remove`` WITHOUT ``--force``.
* a directory under ``.claude/worktrees/`` that git does not register is never moved: an EMPTY one
  (no files at any depth) is removed in place, bottom-up with ``Path.rmdir``, which itself refuses a
  directory that is not empty; one that holds files is listed with them and refused.

``--archive`` IS THE ONE OPT-IN EXCEPTION, and it is that incident's loop done as a door (user directive
2026-10-19). The same consumer later archived ~67 trees by hand into ``output/worktree-archive/`` and
nothing aged them out: 1.13 M ignored files, and a pre-commit ignored-file scan timed out on every commit.
The default does not change, because a move is only safe when every step before the removal is
VERIFIED, and the door can only verify that when somebody declared where work goes. So ``--archive``
needs ``--prune`` and a declared ``archive`` root (no guessed default -- :class:`ArchiveNotDeclared`),
and it acts only on a tree that would be pruned but for ignored work: tracked-clean, HEAD on a remote
branch, NOT locked, and referenced by NO live process (command line or cwd contains the tree; a
process table that cannot be read refuses). Then, in order, each step gating the next: delete the
regenerable paths (a ``.venv`` never reaches the archive, nor one nested inside ``output/``); create
``<archive>/<YYYYMMDD>/<tree name>/`` (refused if it exists) with :data:`ARCHIVE_NOTE` naming HEAD and
branch; move every remaining ignored path by RENAME; verify each destination exists and each source is
gone; and only then ``git worktree remove`` without ``--force``. Any failure keeps the tree. No branch
is ever deleted.

``--expire-archive`` ages the archive out by the STAMP NAME (``YYYYMMDD`` or ``YYYYMMDD-HHMMSS``, a
real calendar date), never mtime -- a move preserves mtimes, so a fresh archive of old files reads as
ancient. Older than ``archive_days``: removed. Younger: kept, its regenerable paths deleted at once. A
name that is not a stamp is listed and never deleted. Dry like the census; ``--prune`` acts.

THE CEILING (``file_ceiling`` over ``ceiling_roots``) is printed with every census, so the human who
runs the door sees it, counted by a walk that STOPS at the ceiling. The archive's own facts -- its
declaration, the liveness read, the stamp, the purge, the count -- are :mod:`lab_commons.dev.worktreearchive`.

Declared once, in the MAIN checkout's ``pyproject.toml``::

    [tool.lab_commons.worktrees]
    regenerable = ['*.gen']                 # extra regenerable patterns (read per tree)
    archive = 'output/worktree-archive'     # opt-in; relative to the main checkout
    archive_days = 14                       # required with archive
    file_ceiling = 200000                   # optional; requires ceiling_roots
    ceiling_roots = ['output']
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final

from lab_commons.dev.checkout import git_out, orphan_directories, worktrees
from lab_commons.dev.worktreearchive import (
    ARCHIVE_NOTE,
    TABLE,
    Archive,
    Processes,
    archive_policy,
    ceiling_warning,
    expire_stamps,
    local_today,
    matches,
    processes,
    purge,
    remove,
)
from lab_commons.log import emit

__all__ = ['REGENERABLE', 'blockers', 'ceiling', 'expire', 'main', 'prune', 'regenerable']

#: THE family default: ignored path parts (``fnmatch`` patterns) a build or a tool regenerates on
#: demand, so they may go with their tree. A repo adds its own in ``[tool.lab_commons.worktrees]
#: regenerable`` of its ``pyproject.toml``; anything ignored and not matched is WORK and blocks.
REGENERABLE: Final = frozenset(
    {
        '__pycache__',
        '.pytest_cache',
        '.ruff_cache',
        '.mypy_cache',
        '.venv',
        'node_modules',
        '.verify',
        '*.egg-info',
        '*.py[cod]',
        '.coverage*',
        'htmlcov',
        'build',
        'dist',
        'target',
        '__version__.py',
    }
)

_SHOWN: Final = 5


def _declared(directory: Path) -> dict[str, Any]:
    manifest = directory / 'pyproject.toml'
    if not manifest.is_file():
        return {}
    with manifest.open('rb') as handle:
        return tomllib.load(handle).get('tool', {}).get('lab_commons', {}).get('worktrees', {})


def regenerable(tree: Path) -> frozenset[str]:
    """:data:`REGENERABLE` plus *tree*'s declared ``[tool.lab_commons.worktrees] regenerable``."""
    extra = _declared(tree).get('regenerable', [])
    if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
        msg = f'{TABLE} regenerable in {tree / "pyproject.toml"} must be a list of strings, got {extra!r}'
        raise ValueError(msg)
    return REGENERABLE | frozenset(extra)


def _policy(root: Path) -> Archive:
    return archive_policy(_declared(root), root / 'pyproject.toml')


def _is_regenerable(path: str, patterns: frozenset[str]) -> bool:
    return any(matches(part, patterns) for part in PurePosixPath(path).parts)


def _status(tree: Path) -> list[tuple[str, str]] | None:
    out = git_out(tree, 'status', '--porcelain', '--ignored=matching')
    if out is None:
        return None
    return [(line[:2], line[3:].strip().rstrip('/')) for line in out.splitlines()]


def blockers(tree: Path) -> tuple[str, ...] | None:
    """Every path that stops *tree* from being removed: modified, staged, untracked, or ignored work."""
    rows = _status(tree)
    if rows is None:
        return None
    patterns = regenerable(tree)
    return tuple(
        f'{code.strip() or "?"} {path}' for code, path in rows if not (code == '!!' and _is_regenerable(path, patterns))
    )


def _files(directory: Path) -> list[str]:
    return sorted(str(p.relative_to(directory)) for p in directory.rglob('*') if not p.is_dir())


def _shown(paths: tuple[str, ...] | list[str]) -> str:
    more = f' (+{len(paths) - _SHOWN} more)' if len(paths) > _SHOWN else ''
    return ', '.join(paths[:_SHOWN]) + more


def _move(source: Path, target: Path) -> None:
    """A RENAME, never a copy-then-delete: one filesystem step that either happened or did not."""
    source.rename(target)


def _locked(root: Path) -> frozenset[Path] | None:
    out = git_out(root, 'worktree', 'list', '--porcelain')
    if out is None:
        return None
    found: set[Path] = set()
    path: Path | None = None
    for line in out.splitlines():
        if line.startswith('worktree '):
            path = Path(line.removeprefix('worktree ').strip())
        elif (line == 'locked' or line.startswith('locked ')) and path is not None:
            found.add(path.resolve())
    return frozenset(found)


@dataclass
class _Archiving:
    """One ``--archive`` pass: the policy, the main checkout, and the process table read at most once."""

    main: Path
    policy: Archive
    stamp: str
    source: Processes
    locked: frozenset[Path]
    _table: tuple[tuple[str, str], ...] | None = field(default=None, init=False)
    _read: bool = field(default=False, init=False)

    def live(self, tree: Path) -> bool | None:
        """Whether a process names *tree* in its command line or stands in it; ``None``: cannot say."""
        if not self._read:
            got = self.source()
            self._table, self._read = (None if got is None else tuple(got)), True
        if not self._table:
            return None
        needles = {os.path.normcase(str(tree)), os.path.normcase(tree.as_posix())}
        for cmdline, cwd in self._table:
            line, here = os.path.normcase(cmdline), os.path.normcase(cwd)
            if any(n in line or here == n or here.startswith(n + os.sep) for n in needles):
                return True
        return False


def _refusal(path: Path, plan: _Archiving) -> str | None:
    """Why *path* may not be archived at all, before anything in it is touched; ``None``: it may."""
    if path.resolve() in plan.locked:
        return 'locked -- git worktree unlock it first'
    live = plan.live(path)
    if live is None:
        return 'process liveness could not be determined -- nothing moved'
    return 'a live process references it -- nothing moved' if live else None


def _carry(path: Path, branch: str, work: list[str], dest: Path) -> str | None:
    """Purge regenerables, note HEAD, rename *work* into *dest*, verify; the failure, or ``None``."""
    patterns = regenerable(path)
    try:
        for code, rel in _status(path) or ():
            if code == '!!' and _is_regenerable(rel, patterns):
                remove(path / rel)
        for rel in work:
            purge(path / rel, patterns)
        dest.mkdir(parents=True)
        sha = (git_out(path, 'rev-parse', 'HEAD') or '').strip()
        (dest / ARCHIVE_NOTE).write_text(f'head {sha}\nbranch {branch}\npath {path}\n', encoding='utf-8')
        for rel in (r for r in work if (path / r).exists()):
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            _move(path / rel, dest / rel)
    except OSError as error:
        return f'archive step failed, tree kept: {error}'
    stranded = [rel for rel in work if (path / rel).exists() or not (dest / rel).exists()]
    return f'move not verified, tree kept: {_shown(stranded)}' if stranded else None


def _archive_line(path: Path, branch: str, work: list[str], plan: _Archiving, *, apply: bool) -> str:
    """Archive one tree whose only blockers are ignored *work*: every step gates the next."""
    head = f'{path}  [{branch}]'
    refused = _refusal(path, plan)
    if refused:
        return f'kept     {head}  ({refused})'
    dest = plan.main / plan.policy.root / plan.stamp / path.name
    if not apply:
        return f'archivable {head}  -> {dest}  ({_shown(work)})'
    failed = f'archive destination exists: {dest}' if dest.exists() else _carry(path, branch, work, dest)
    if failed is None and git_out(plan.main, 'worktree', 'remove', str(path)) is None:
        failed = f'archived to {dest}; git worktree remove refused, tree kept'
    return f'FAILED   {head}  ({failed})' if failed else f'archived {head}  -> {dest}'


def _tree_line(root: Path, path: Path, branch: str, *, apply: bool, plan: _Archiving | None) -> str:
    blocking = blockers(path)
    if blocking is None:
        return f'kept     {path}  [{branch}]  (unreadable)'
    archivable = bool(blocking) and plan is not None and all(b.startswith('!! ') for b in blocking)
    if blocking and not archivable:
        return f'kept     {path}  [{branch}]  (not clean -- commit or clear first: {_shown(blocking)})'
    if not (git_out(path, 'branch', '-r', '--contains', 'HEAD') or '').strip():
        return f'kept     {path}  [{branch}]  (HEAD is on no remote branch)'
    if archivable and plan is not None:
        return _archive_line(path, branch, [b.removeprefix('!! ') for b in blocking], plan, apply=apply)
    if not apply:
        return f'prunable {path}  [{branch}]'
    removed = git_out(root, 'worktree', 'remove', str(path)) is not None
    return f'{"removed " if removed else "FAILED  "} {path}  [{branch}]'


def _orphan_line(orphan: Path, *, apply: bool) -> str:
    files = _files(orphan)
    if files:
        return f'kept     {orphan}  (unregistered, holds files -- a human decides: {_shown(files)})'
    if not apply:
        return f'orphan   {orphan}  (unregistered and empty; --prune removes it)'
    try:
        for directory in sorted(orphan.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            directory.rmdir()
        orphan.rmdir()
    except OSError as error:
        return f'FAILED   {orphan}  ({error})'
    return f'removed  {orphan}  (unregistered and empty)'


def prune(
    root: Path,
    *,
    apply: bool,
    archive: bool = False,
    today: dt.date | None = None,
    processes: Processes = processes,
) -> tuple[str, ...] | None:
    """One line per non-main worktree and orphan directory; with *apply*, act. ``None``: unreadable.

    *archive* (opt-in) moves a tree's ignored work into the declared archive instead of refusing the
    tree (module docstring); it raises :class:`~lab_commons.dev.worktreearchive.ArchiveNotDeclared`
    before touching anything.
    """
    trees = worktrees(root)
    orphans = orphan_directories(root)
    locked = _locked(root) if archive else frozenset()
    if trees is None or orphans is None or locked is None:
        return None
    plan = None
    if archive:
        main = trees[0][0]
        plan = _Archiving(main, _policy(main), (today or local_today()).strftime('%Y%m%d'), processes, locked)
    out = [_tree_line(root, path, branch, apply=apply, plan=plan) for path, branch in trees[1:]]
    out += [_orphan_line(orphan, apply=apply) for orphan in orphans]
    if apply:
        git_out(root, 'worktree', 'prune')
    return tuple(out)


def expire(root: Path, *, apply: bool, today: dt.date | None = None) -> tuple[str, ...]:
    """The main checkout *root*'s archive retention (``--expire-archive``); with *apply*, act."""
    policy = _policy(root)
    return expire_stamps(root / policy.root, policy, regenerable(root), apply=apply, on=today or local_today())


def ceiling(root: Path) -> str | None:
    """The declared ``file_ceiling`` warning for *root*, or ``None`` (undeclared, or under it)."""
    return ceiling_warning(root, _declared(root))


def main(argv: list[str] | None = None) -> int:
    """List worktrees and leftovers; ``--prune`` removes the clean ones. Exit 1 on a failure, 2 when unread."""
    parser = argparse.ArgumentParser(prog='python -m lab_commons.dev.worktrees', description=main.__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--prune', action='store_true', help='act; without it every mode only reports')
    parser.add_argument(
        '--archive', action='store_true', help='OPT-IN: move ignored work to the declared archive, then remove'
    )
    parser.add_argument('--expire-archive', action='store_true', help='age out <archive>/<stamp>/ by its name')
    args = parser.parse_args(argv)
    if args.archive and not args.prune:
        parser.error('--archive requires --prune')
    root = args.root.resolve()
    try:
        if args.expire_archive:
            lines: tuple[str, ...] | None = expire(root, apply=args.prune)
        else:
            lines = prune(root, apply=args.prune, archive=args.archive)
        warning = ceiling(root)
    except ValueError as error:
        emit(f'{error}; nothing was touched')
        return 2
    if lines is None:
        emit(f'git could not read the worktrees of {args.root}; nothing was touched')
        return 2
    for line in (*lines, *([warning] if warning else [])):
        emit(line)
    return 1 if any(line.startswith('FAILED') for line in lines) else 0


if __name__ == '__main__':
    raise SystemExit(main())
