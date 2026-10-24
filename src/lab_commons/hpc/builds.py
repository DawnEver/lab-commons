r"""CLUSTER BUILD REUSE -- a venv and a native build keyed by their REAL inputs, never by the commit.

THE REPO DECLARES ITS INPUTS ONCE, in its pyproject (read at the commit being judged)::

    [tool.lab_commons.hpc]
    install = "uv sync --extra dev"                         # the venv's build command (required)
    needs = ["cargo", "cc"]                                 # login-node commands beyond git and uv
    dependency_inputs = ["pyproject.toml", "uv.lock"]      # what the venv is a function of
    source_roots = ["src"]                                  # put on PYTHONPATH from each sha's tree
    artifact_paths = ["cases"]                              # what a job reads beside them (lab_commons.hpc.artifact)
    native = { inputs = ["rust"], build = "pip install --target $LAB_CI_NATIVE ./rust/pybind" }
    timeout_s = { incremental = 300, full = 3000 }          # pytest --timeout per run scope (absent: the repo's own)
    item_wall = { multiple = 10, floor_s = 300, unmeasured_s = 3600 }  # per-test kill bound (these are the defaults)

A venv lives in ``~/ci/envs/<env key>`` and a native build in ``~/ci/native/<native key>``; each sha is
only a ``git worktree`` whose ``source_roots`` reach the venv through ``PYTHONPATH`` -- no per-sha
editable install. A fresh sha with unchanged inputs therefore builds NOTHING. The native build runs with
``$LAB_CI_NATIVE`` naming its output directory, which is put on ``PYTHONPATH`` after the source roots.

A key is a SHA-256 over the git object ids of the declared inputs AT THE COMMIT (``git rev-parse
<sha>:<path>`` -- a read-only call; an absent path keys as absent) plus the build command and the
interpreter request. The committed ``uv.lock`` is one of those inputs and it pins every dependency revision
(PIN + FRESHNESS, user ruling 2026-10-17): the lock's hash keys the venv, a new lock rebuilds it, and no
remote is asked -- the same commit builds the same venv on every host and every day.
EVERY ENV HAS A KEY, AND THE KEY IS EVERY INPUT THAT BUILDS IT: the install command, the needs and the
interpreter request always; the declared inputs when there are some, else the commit itself (a venv
per commit). Measured 2026-10-10: a resubmit with a corrected install reused the tree's venv built by
the first, because the venv was keyed by the tree alone.
"""

from __future__ import annotations

import hashlib
import math
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

import lab_commons.launch as _door
from lab_commons.hpc.declared import InvalidDeclaration, kit_tables

__all__ = [
    'FULL',
    'INCREMENTAL',
    'SCOPES',
    'Builds',
    'ItemWall',
    'builds_at',
    'key_of',
    'read_builds',
]

_GIT: Final = shutil.which('git') or 'git'

#: Hex digits of a build key -- enough to never collide among one user's builds, short in a path.
_KEY_HEX: Final = 24

#: A run's scope: every collected id, or only the re-run ids that complete a base part (``RunSpec.only``).
FULL: Final = 'full'
INCREMENTAL: Final = 'incremental'
SCOPES: Final = (FULL, INCREMENTAL)


@dataclass(frozen=True)
class ItemWall:
    """THE PER-TEST WALL the item runner enforces from OUTSIDE pytest (:mod:`lab_commons.hpc.pytest_item`).

    pytest-timeout runs inside the test's interpreter and cannot interrupt a C loop holding the GIL. Measured
    on Ada: a test spun at 100% CPU for 20 h, and a verdict waited 4 h on two hung items under a 21 h Slurm
    limit. A test with a measured duration is bounded at ``multiple`` times it, never under ``floor_s``; one
    never measured gets ``unmeasured_s``. These defaults are the one place the numbers live.
    """

    multiple: float = 10.0
    floor_s: int = 300
    unmeasured_s: int = 3600

    def bound(self, measured_s: float | None) -> int:
        """Whole seconds a test of measured duration *measured_s* (``None``: never measured) may run."""
        if measured_s is None:
            return self.unmeasured_s
        return max(self.floor_s, math.ceil(self.multiple * measured_s))


@dataclass(frozen=True)
class Builds:
    """The declaration, plus the keys computed for one commit (empty when undeclared)."""

    dependency_inputs: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    native_inputs: tuple[str, ...] = ()
    native_build: str = ''
    install: str = ''
    needs: tuple[str, ...] = ()
    env_key: str = ''
    native_key: str = ''
    #: ``{scope: seconds}`` handed to pytest as ``--timeout``; a scope absent here keeps the repo's own default.
    timeout_s: dict[str, int] = field(default_factory=dict)
    #: Repository paths a job's artifact carries beside the source roots (:mod:`lab_commons.hpc.artifact`).
    artifact_paths: tuple[str, ...] = ()
    #: The per-test wall the item runner kills a hung test at (:class:`ItemWall`).
    item_wall: ItemWall = field(default_factory=ItemWall)


_ITEM_WALL_KEYS: Final = frozenset({'multiple', 'floor_s', 'unmeasured_s'})


def _item_wall(raw: dict[str, object]) -> ItemWall:
    """The ``item_wall`` table; an unknown key or a non-positive number is refused by name."""
    bad = set(raw) - _ITEM_WALL_KEYS or any(
        isinstance(v, bool) or not isinstance(v, int | float) or v <= 0 for v in raw.values()
    )
    if bad:
        msg = f'[tool.lab_commons.hpc] item_wall {raw!r}: keys {sorted(_ITEM_WALL_KEYS)}, values positive numbers'
        raise InvalidDeclaration(msg)
    return ItemWall(**raw)  # type: ignore[arg-type]


def read_builds(text: str) -> Builds:
    """The ``[tool.lab_commons.hpc]`` declaration of pyproject *text*; absent is an empty :class:`Builds`."""
    raw = kit_tables(text).get('hpc', {})
    native = raw.get('native', {})
    builds = Builds(
        install=str(raw.get('install', '')),
        needs=tuple(raw.get('needs', ())),
        dependency_inputs=tuple(raw.get('dependency_inputs', ())),
        source_roots=tuple(raw.get('source_roots', ())),
        artifact_paths=tuple(raw.get('artifact_paths', ())),
        native_inputs=tuple(native.get('inputs', ())),
        native_build=str(native.get('build', '')),
        timeout_s=dict(raw.get('timeout_s', {})),
        item_wall=_item_wall(dict(raw.get('item_wall', {}))),
    )
    bad = {k: v for k, v in builds.timeout_s.items() if k not in SCOPES or not isinstance(v, int) or v <= 0}
    if bad:
        msg = f'[tool.lab_commons.hpc] timeout_s {bad!r}: keys are scopes {SCOPES}, values positive whole seconds'
        raise InvalidDeclaration(msg)
    if builds.dependency_inputs and not builds.source_roots:
        msg = '[tool.lab_commons.hpc] dependency_inputs needs source_roots: a shared venv holds no sha source'
        raise InvalidDeclaration(msg)
    if bool(builds.native_inputs) != bool(builds.native_build):
        msg = '[tool.lab_commons.hpc] native needs both inputs and build'
        raise InvalidDeclaration(msg)
    return builds


def cargo_target(native_key: str) -> str:
    """Cargo's target dir for the native build keyed *native_key*, relative to ``$HOME``.

    A target dir may be shared only by builds of IDENTICAL native inputs: cargo's freshness is mtime-based,
    so one dir shared by every native build reused artifacts compiled from another tree's source.
    Measured 2026-10-14 on Ada: the pybind crate compiled against a stale ``mesh_core`` and every downstream
    deploy was refused. Used by the cluster runs (:mod:`lab_commons.hpc.shell`) and the deploy alike.
    """
    if not native_key:
        msg = 'a cargo target dir needs a native key'
        raise ValueError(msg)
    return f'ci/cargo-target/{native_key}'


def key_of(oids: dict[str, str], *recipe: str) -> str:
    """The key of a build whose inputs have git object ids *oids*, made by *recipe* (command, interpreter)."""
    text = '\n'.join([*recipe, *(f'{path} {oid}' for path, oid in sorted(oids.items()))])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:_KEY_HEX]


def _oids(repo: Path, sha: str, paths: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        done = _door.run(
            [_GIT, '-C', str(repo), 'rev-parse', '--verify', '-q', f'{sha}:{path.rstrip("/")}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
        out[path] = done.stdout.strip() if done.returncode == 0 else 'absent'
    return out


def builds_at(repo: Path, sha: str, *, python: str) -> Builds:
    """The declaration commit *sha* of *repo* carries, with its keys -- every git call here only READS.

    Raises:
        ValueError: the commit declares no ``[tool.lab_commons.hpc] install`` -- a venv with no build command.

    """
    text = _door.run(
        [_GIT, '-C', str(repo), 'show', f'{sha}:pyproject.toml'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    builds = read_builds(text)
    if not builds.install:
        msg = f'{sha[:12]} declares no [tool.lab_commons.hpc] install, e.g. install = "uv sync --extra dev"'
        raise ValueError(msg)
    oids = _oids(repo, sha, builds.dependency_inputs) if builds.dependency_inputs else {'commit': sha}
    env_key = key_of(oids, builds.install, python, *builds.needs)
    native_key = ''
    if builds.native_inputs:
        native_key = key_of(_oids(repo, sha, builds.native_inputs), builds.native_build, python, env_key)
    return replace(builds, env_key=env_key, native_key=native_key)
