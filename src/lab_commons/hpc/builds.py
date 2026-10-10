r"""CLUSTER BUILD REUSE -- a venv and a native build keyed by their REAL inputs, never by the commit.

THE REPO DECLARES ITS INPUTS ONCE, in its pyproject (read at the commit being judged)::

    [tool.lab_commons.hpc]
    dependency_inputs = ["pyproject.toml", "uv.lock"]      # what the venv is a function of
    source_roots = ["src"]                                  # put on PYTHONPATH from each sha's tree
    native = { inputs = ["rust"], build = "pip install --target $LAB_CI_NATIVE ./rust/pybind" }

A venv lives in ``~/ci/envs/<env key>`` and a native build in ``~/ci/native/<native key>``; each sha is
only a ``git worktree`` whose ``source_roots`` reach the venv through ``PYTHONPATH`` -- no per-sha
editable install. A fresh sha with unchanged inputs therefore builds NOTHING. The native build runs with
``$LAB_CI_NATIVE`` naming its output directory, which is put on ``PYTHONPATH`` after the source roots.

A key is a SHA-256 over the git object ids of the declared inputs AT THE COMMIT (``git rev-parse
<sha>:<path>`` -- a read-only call; an absent path keys as absent) plus the build command and the
interpreter request. A git dependency that FLOATS (a ``name @ git+<url>[@<ref>]`` requirement, or a
``[tool.uv.sources]`` ``git`` table without a ``rev``) adds the CURRENT remote tip of its ref to the env
key (``git ls-remote`` -- read-only, the pyproject is the one list), so a new upstream tip rebuilds the
venv and an unchanged one reuses it; a 40-hex pin is already in the hashed pyproject.
Undeclared: no key, and the run builds a venv per tree as before.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

__all__ = ['Builds', 'builds_at', 'floating_git', 'key_of', 'read_builds', 'remote_tip']

_GIT: Final = shutil.which('git') or 'git'

#: Hex digits of a build key -- enough to never collide among one user's builds, short in a path.
_KEY_HEX: Final = 24


@dataclass(frozen=True)
class Builds:
    """The declaration, plus the keys computed for one commit (empty when undeclared)."""

    dependency_inputs: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    native_inputs: tuple[str, ...] = ()
    native_build: str = ''
    env_key: str = ''
    native_key: str = ''


def read_builds(text: str) -> Builds:
    """The ``[tool.lab_commons.hpc]`` declaration of pyproject *text*; absent is an empty :class:`Builds`."""
    raw = tomllib.loads(text).get('tool', {}).get('lab_commons', {}).get('hpc', {})
    native = raw.get('native', {})
    builds = Builds(
        dependency_inputs=tuple(raw.get('dependency_inputs', ())),
        source_roots=tuple(raw.get('source_roots', ())),
        native_inputs=tuple(native.get('inputs', ())),
        native_build=str(native.get('build', '')),
    )
    if builds.dependency_inputs and not builds.source_roots:
        msg = '[tool.lab_commons.hpc] dependency_inputs needs source_roots: a shared venv holds no sha source'
        raise ValueError(msg)
    if bool(builds.native_inputs) != bool(builds.native_build):
        msg = '[tool.lab_commons.hpc] native needs both inputs and build'
        raise ValueError(msg)
    return builds


def key_of(oids: dict[str, str], *recipe: str) -> str:
    """The key of a build whose inputs have git object ids *oids*, made by *recipe* (command, interpreter)."""
    text = '\n'.join([*recipe, *(f'{path} {oid}' for path, oid in sorted(oids.items()))])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:_KEY_HEX]


def _oids(repo: Path, sha: str, paths: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        done = subprocess.run(
            [_GIT, '-C', str(repo), 'rev-parse', '--verify', '-q', f'{sha}:{path.rstrip("/")}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
        out[path] = done.stdout.strip() if done.returncode == 0 else 'absent'
    return out


_PINNED: Final = re.compile(r'[0-9a-f]{40}')


def _requirements(raw: dict[str, Any]) -> Iterator[str]:
    project = raw.get('project', {})
    yield from project.get('dependencies', ())
    for group in (*project.get('optional-dependencies', {}).values(), *raw.get('dependency-groups', {}).values()):
        yield from (r for r in group if isinstance(r, str))


def floating_git(text: str) -> tuple[tuple[str, str], ...]:
    """``(url, ref)`` of every git dependency of pyproject *text* NOT pinned to a commit; ref ``HEAD`` when unnamed."""
    raw = tomllib.loads(text)
    found: set[tuple[str, str]] = set()
    for req in _requirements(raw):
        _, sep, ref_url = req.partition('git+')
        if not sep:
            continue
        parts = urlsplit(re.split(r'[\s;#]', ref_url, maxsplit=1)[0])
        path, _, ref = parts.path.partition('@')
        found.add((parts._replace(path=path).geturl(), ref or 'HEAD'))
    for source in raw.get('tool', {}).get('uv', {}).get('sources', {}).values():
        for entry in source if isinstance(source, list) else [source]:
            if 'git' in entry and 'rev' not in entry:
                found.add((entry['git'], entry.get('branch') or entry.get('tag') or 'HEAD'))
    return tuple(sorted((url, ref) for url, ref in found if not _PINNED.fullmatch(ref)))


def remote_tip(url: str, ref: str) -> str:
    """The commit *ref* of the remote at *url* names now -- ``git ls-remote``, which writes nothing."""
    done = subprocess.run(
        [_GIT, 'ls-remote', url, ref], capture_output=True, text=True, encoding='utf-8', check=False, timeout=60
    )
    words = done.stdout.split()
    if done.returncode != 0 or not words:
        msg = f'git ls-remote {url} {ref} named no commit (exit {done.returncode}): {done.stderr.strip()}'
        raise RuntimeError(msg)
    return words[0]


def builds_at(
    repo: Path, sha: str, *, install: str, python: str, tip: Callable[[str, str], str] = remote_tip
) -> Builds:
    """The declaration commit *sha* of *repo* carries, with its keys -- every git call here only READS."""
    text = subprocess.run(
        [_GIT, '-C', str(repo), 'show', f'{sha}:pyproject.toml'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    builds = read_builds(text)
    env_key = ''
    if builds.dependency_inputs:
        oids = _oids(repo, sha, builds.dependency_inputs)
        oids.update({f'git+{url}@{ref}': tip(url, ref) for url, ref in floating_git(text)})
        env_key = key_of(oids, install, python)
    native_key = ''
    if builds.native_inputs:
        native_key = key_of(_oids(repo, sha, builds.native_inputs), builds.native_build, python, env_key)
    return replace(builds, env_key=env_key, native_key=native_key)
