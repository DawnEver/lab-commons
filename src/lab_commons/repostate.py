"""WHERE ONE REPO'S CROSS-SESSION STATE LIVES: ``<machine home>/state/<repo>/<kind>/`` (user ruling D32, 2026-11-17).

A record that must outlive a checkout and be shared by every worktree of a repo -- task bindings, consent
records, PR watcher state -- lives here and nowhere else; :func:`state_dir` is the one place that path is computed.

THE REPO KEY (:func:`repo_key`) is ``<name>-<digest>``: *name* is the basename of the main checkout (the
parent of ``git rev-parse --git-common-dir``; a bare repo's own directory), *digest* the first 12 hex of the
SHA-256 of that common dir as the filesystem resolves it (:func:`lab_commons.home.canonical`), case-folded
where the filesystem ignores case. Every worktree shares the common dir, so they share the key; two clones --
even of one remote, even under one basename -- have two common dirs, so two keys. The name keeps it readable;
the digest keeps it unambiguous. Moving a checkout moves its key: its state stays behind under the old one.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

from lab_commons.home import canonical, part

__all__ = ['DIGEST_HEX', 'NotARepo', 'repo_key', 'state_dir']

#: The digest's length in hex: 48 bits, so two clones on one machine never collide in practice.
DIGEST_HEX: Final = 12

_GIT: Final = shutil.which('git') or 'git'
_CASELESS: Final = frozenset({'win32', 'darwin'})


class NotARepo(ValueError):
    """A path outside every git repository has no repo state."""


def _common_dir(where: Path) -> Path:
    done = subprocess.run(
        [_GIT, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
        cwd=where,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        msg = f'{where} is not inside a git repository, so it has no repo state: {done.stderr.strip()}'
        raise NotARepo(msg)
    return canonical(Path(done.stdout.strip()))


def repo_key(where: Path) -> str:
    """``<name>-<digest>`` of the repo *where* lies in -- the same from every worktree of it."""
    common = _common_dir(where)
    checkout = common.parent if common.name == '.git' else common
    text = common.as_posix()
    text = text.casefold() if sys.platform in _CASELESS else text
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:DIGEST_HEX]
    return f'{checkout.name}-{digest}'


def state_dir(where: Path, kind: str) -> Path:
    """``<machine home>/state/<repo_key(where)>/<kind>``; computed only, never created. *kind* is one name."""
    if kind in {'', '.', '..'} or Path(kind).name != kind or '/' in kind or '\\' in kind:
        msg = f'kind {kind!r} must be one directory name'
        raise ValueError(msg)
    return part('state') / repo_key(where) / kind
