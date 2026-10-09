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
interpreter request. Undeclared: no key, and the verdict builds a venv per tree as before.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

__all__ = ['Builds', 'builds_at', 'key_of', 'read_builds']

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


def builds_at(repo: Path, sha: str, *, install: str, python: str) -> Builds:
    """The declaration commit *sha* of *repo* carries, with its keys -- every git call here only READS."""
    text = subprocess.run(
        [_GIT, '-C', str(repo), 'show', f'{sha}:pyproject.toml'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    builds = read_builds(text)
    env_key = key_of(_oids(repo, sha, builds.dependency_inputs), install, python) if builds.dependency_inputs else ''
    native_key = ''
    if builds.native_inputs:
        native_key = key_of(_oids(repo, sha, builds.native_inputs), builds.native_build, python, env_key)
    return replace(builds, env_key=env_key, native_key=native_key)
