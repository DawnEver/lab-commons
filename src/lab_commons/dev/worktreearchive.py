"""The ARCHIVE half of the worktree door: its declaration, its liveness read, its retention, its ceiling.

:mod:`lab_commons.dev.worktrees` decides WHICH tree is archived and runs the steps; this module holds
the parts that are true of the archive itself, so neither half restates the other. Every function
takes the already-read ``[tool.lab_commons.worktrees]`` table, a path or a pattern set as an argument:
the door reads the manifest once and the facts here are tested without one.

WHY AN ARCHIVE NEEDS RETENTION AT ALL (a consumer, 2026-10-18/19): ~67 trees were archived by hand
into ``output/worktree-archive/<stamp>/`` and nothing aged them out. One stamp held whole ``.venv``
copies; the checkout reached 1.13 M ignored files and a pre-commit ``git ls-files --ignored`` scan
timed out on every commit. Hence three rules, each the cheap form of what that cost:

* a ``.venv`` (any regenerable path, :data:`lab_commons.dev.worktrees.REGENERABLE`) never reaches
  the archive, and one already inside a young stamp is deleted at once (:func:`purge`);
* a stamp ages out by its NAME (:func:`stamp_date`), never mtime -- a rename preserves mtimes, so a
  fresh archive of old files reads as ancient, and a copied one as new. A name that is not a valid
  ``YYYYMMDD`` / ``YYYYMMDD-HHMMSS`` calendar stamp is somebody's, listed and never deleted;
* the count that would have warned is a walk that STOPS at the ceiling (:func:`count_files`): the
  full walk of a million files is the cost the warning exists to prevent.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Final

from lab_commons.dev.stoprun import process_table

__all__ = [
    'ARCHIVE_NOTE',
    'TABLE',
    'Archive',
    'ArchiveNotDeclared',
    'Processes',
    'archive_policy',
    'ceiling_warning',
    'count_files',
    'expire_stamps',
    'local_today',
    'matches',
    'processes',
    'purge',
    'remove',
    'stamp_date',
]

#: The manifest table every key of the door lives in, spelled once for every message that names one.
TABLE: Final = '[tool.lab_commons.worktrees]'

#: The file written beside an archived tree's content: its HEAD sha, branch and former path, so the
#: archive says which commit the work belonged to after the tree is gone.
ARCHIVE_NOTE: Final = 'WORKTREE.txt'

_STAMP: Final = re.compile(r'(\d{8})(?:-(\d{6}))?')

#: ``(command line, cwd)`` for every process on the box, or ``None`` when the table could not be read.
Processes = Callable[[], Iterable[tuple[str, str]] | None]


class ArchiveNotDeclared(ValueError):
    """``--archive`` / ``--expire-archive`` without a valid ``archive`` + ``archive_days`` declaration."""


@dataclass(frozen=True)
class Archive:
    """Where archived work goes (relative to the main checkout) and how many days a stamp is kept."""

    root: Path
    days: int


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def archive_policy(table: Mapping[str, Any], manifest: Path) -> Archive:
    """The declared archive in *table* (read from *manifest*), or :class:`ArchiveNotDeclared` naming the key.

    NO DEFAULT, for the reason :mod:`lab_commons.dev.datedlog` gives for its base: where a repo keeps
    its output is that repo's answer, and a guessed one would move work somewhere nobody looks.
    """
    where, days = table.get('archive'), table.get('archive_days')
    if not isinstance(where, str) or not where:
        msg = f'--archive needs {TABLE} archive = "<relative dir>" in {manifest}; got {where!r}'
        raise ArchiveNotDeclared(msg)
    relative = PurePosixPath(where.replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts or ':' in where:
        msg = f'{TABLE} archive = {where!r} in {manifest} must be relative to the checkout and stay inside it'
        raise ArchiveNotDeclared(msg)
    if not _positive(days):
        msg = f'{TABLE} archive_days in {manifest} must be a positive integer beside archive = {where!r}; got {days!r}'
        raise ArchiveNotDeclared(msg)
    return Archive(Path(*relative.parts), int(days))


def matches(name: str, patterns: frozenset[str]) -> bool:
    """Whether one path PART *name* matches any regenerable ``fnmatch`` pattern."""
    return any(fnmatch(name, pattern) for pattern in patterns)


def _writable(function: Callable[[str], object], path: str, _error: BaseException) -> None:
    """``rmtree``'s retry for a read-only entry (git's object files on Windows): clear the bit, retry."""
    Path(path).chmod(stat.S_IWRITE)
    function(path)


def remove(path: Path) -> None:
    """Delete *path*: a directory tree (read-only entries included, links not followed) or one file."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onexc=_writable)
    else:
        path.unlink(missing_ok=True)


def purge(top: Path, patterns: frozenset[str]) -> int:
    """Delete every regenerable entry at or under *top*, never descending into one; how many went."""
    if matches(top.name, patterns):
        remove(top)
        return 1
    gone, stack = 0, [top] if top.is_dir() and not top.is_symlink() else []
    while stack:
        with os.scandir(stack.pop()) as entries:
            listed = list(entries)
        for entry in listed:
            if matches(entry.name, patterns):
                remove(Path(entry.path))
                gone += 1
            elif entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
    return gone


def _cwd(pid: int) -> str:
    """*pid*'s working directory where the kernel publishes it (``/proc``), else ``''``."""
    try:
        return str(Path(f'/proc/{pid}/cwd').readlink())
    except OSError:
        return ''


def processes() -> tuple[tuple[str, str], ...] | None:
    """``(command line, cwd)`` for every process, or ``None`` when liveness cannot be determined.

    The command line comes from :func:`lab_commons.dev.stoprun.process_table`; an empty or unreadable
    table is ``None``, never "nothing runs". The cwd is read from ``/proc`` where it exists. Windows
    keeps it in the target's PEB, which only ``psutil`` reads, and ``psutil`` is outside the dev tier
    (``test_dev_gate``); there a shell parked in a tree is caught by the RENAME instead, which Windows
    refuses for a directory that is some process's cwd -- the failed move keeps the tree.
    """
    try:
        table = process_table()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not table:
        return None
    return tuple((cmdline, _cwd(pid)) for pid, (_, cmdline) in table.items())


def local_today() -> dt.date:
    """The LOCAL calendar day: a stamp names the day a human would call it, not an instant."""
    return dt.datetime.now().astimezone().date()


def stamp_date(name: str) -> dt.date | None:
    """The calendar day a ``YYYYMMDD`` / ``YYYYMMDD-HHMMSS`` stamp names, or ``None`` when it names none."""
    matched = _STAMP.fullmatch(name)
    if matched is None:
        return None
    day, clock = matched[1], matched[2]
    try:
        named = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
        if clock:
            dt.time(int(clock[:2]), int(clock[2:4]), int(clock[4:]))
    except ValueError:
        return None
    return named


def expire_stamps(
    base: Path, policy: Archive, patterns: frozenset[str], *, apply: bool, on: dt.date
) -> tuple[str, ...]:
    """One line per ``<base>/<stamp>/``; with *apply*, remove the expired and purge the young."""
    if not base.is_dir():
        return ()
    out: list[str] = []
    for entry in sorted(base.iterdir()):
        stamped = stamp_date(entry.name) if entry.is_dir() else None
        if stamped is None:
            out.append(f'unparsed {entry}  (not a YYYYMMDD[-HHMMSS] stamp directory; never deleted)')
            continue
        age = (on - stamped).days
        if age > policy.days:
            if apply:
                remove(entry)
            out.append(f'{"removed " if apply else "expired "} {entry}  ({age} days > archive_days {policy.days})')
            continue
        gone = purge(entry, patterns) if apply else 0
        out.append(f'kept     {entry}  ({age} days{f"; {gone} regenerable path(s) removed" if gone else ""})')
    return tuple(out)


def count_files(roots: Iterable[Path], *, limit: int) -> int:
    """Files under *roots*, counted by an iterative walk that STOPS at *limit*; a missing root counts 0."""
    count, stack = 0, [str(r) for r in roots]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    count += 1
                    if count >= limit:
                        return count
        except OSError:
            continue
    return count


def ceiling_warning(root: Path, table: Mapping[str, Any]) -> str | None:
    """A warning when *root*'s declared ``ceiling_roots`` hold at least ``file_ceiling`` files, else ``None``."""
    limit, roots = table.get('file_ceiling'), table.get('ceiling_roots')
    if limit is None:
        return None
    if not _positive(limit) or not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots):
        msg = (
            f'{TABLE} file_ceiling must be a positive integer and ceiling_roots a non-empty list of paths; '
            f'got {limit!r}, {roots!r}'
        )
        raise ValueError(msg)
    if count_files([root / r for r in roots], limit=limit) < limit:
        return None
    return (
        f'WARNING  {", ".join(roots)} hold at least {limit} files (file_ceiling): run '
        '`python -m lab_commons.dev.worktrees --expire-archive --prune`, or the repo-owned output cleaner'
    )
