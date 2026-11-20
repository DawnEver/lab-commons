"""WHERE AN HPC ACCOUNT KEEPS THINGS: one layout under the account's DECLARED root, nothing hard-coded in a home.

The root is a family fact: ``[hpc.clusters.<c>] root`` (or the account file's ``[deploy] root``), with ``{user}``
filled in. It is on a filesystem the cluster's COMPUTE nodes see (Cirrus: ``/epccfs``, never ``/home``). Every path
below derives from it here, in one place::

    <root>/repos/<repo>          the account's checkouts (the kit is just repos/dev-lab)
    <root>/envs/<key>            one venv per environment key (lab_commons.hpc.builds)
    <root>/native/<key>          one native build per key
    <root>/cache/uv              uv's cache, hardlinked into every env
    <root>/cache/git/<repo>.git  one bare cache per repository
    <root>/cache/packs/<sha>.pack, <root>/cache/collect
    <root>/jobs/<sha>/...        a job's run directories (a job at a commit)
  serving roles only (an account that ticks a forge queue):
    <root>/runs/<sha>/tree       a commit's worktree for a test run
    <root>/runs/<sha>/<part>/    a part's run state
    <root>/leases, <root>/artifacts
    <root>/serve/{bin,lock,log,paused}

Login-only state stays in the login's home and is not part of this layout: the machine file, git credentials, the
dated caches of dev-lab, and the one ``export LAB_ROOT=...`` line the deploy writes into the login profile.

Remote scripts name the root as the shell expression :data:`ROOT` (``"$LAB_ROOT"``). The deploy exports it from
:func:`account_paths`, so a login shell and every ``sbatch`` job (Slurm exports the submitting environment) agree on
it; :data:`REQUIRE_ROOT` refuses, by name and with its door, a shell where it is unset.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    'ARTIFACTS',
    'BIN',
    'CACHES',
    'COLLECT',
    'ENVS',
    'JOBS',
    'LEASES',
    'NATIVE',
    'PACKS',
    'REQUIRE_ROOT',
    'ROOT',
    'ROOT_VAR',
    'RUNS',
    'SERVE',
    'UV_CACHE',
    'AccountPaths',
    'RootUndeclared',
    'account_paths',
    'export_line',
    'local_root',
    'run_tree',
]

#: The environment variable that carries the account's declared root on the cluster.
ROOT_VAR: Final = 'LAB_ROOT'

#: The root as remote scripts spell it (expanded by the remote shell).
ROOT: Final = f'"${ROOT_VAR}"'

#: First line of a remote script that touches the layout: an unset root is a deploy never run, named with its door.
REQUIRE_ROOT: Final = (
    f': "${{{ROOT_VAR}:?{ROOT_VAR} is unset: this account was not deployed with its declared root -- '
    'run python -m dev_lab.dev.hpcaccount deploy-fleet --account <user>}"'
)

#: Shared by every role.
ENVS: Final = f'{ROOT}/envs'
NATIVE: Final = f'{ROOT}/native'
UV_CACHE: Final = f'{ROOT}/cache/uv'
CACHES: Final = f'{ROOT}/cache/git'
PACKS: Final = f'{ROOT}/cache/packs'
COLLECT: Final = f'{ROOT}/cache/collect'
JOBS: Final = f'{ROOT}/jobs'

#: A serving role's only.
RUNS: Final = f'{ROOT}/runs'
LEASES: Final = f'{ROOT}/leases'
ARTIFACTS: Final = f'{ROOT}/artifacts'
SERVE: Final = f'{ROOT}/serve'
BIN: Final = f'{SERVE}/bin'


def run_tree(sha: str) -> str:
    """A commit's worktree for a test run, as remote scripts spell it."""
    return f'{RUNS}/{sha}/tree'


class RootUndeclared(ValueError):
    """An account whose cluster and account file declare no root; the message names where to declare it."""


@dataclass(frozen=True)
class AccountPaths:
    """The concrete layout of one account: every directory it may hold, the serving ones only when it serves."""

    root: str
    serves: bool

    def _under(self, *parts: str) -> str:
        return posixpath.join(self.root, *parts)

    def repo(self, name: str) -> str:
        """A checkout of *name*."""
        return self._under('repos', name)

    @property
    def shared(self) -> tuple[str, ...]:
        """The top-level directories every role holds."""
        return tuple(self._under(p) for p in ('repos', 'envs', 'native', 'cache', 'jobs'))

    @property
    def serving(self) -> tuple[str, ...]:
        """The top-level directories only a serving role holds; empty otherwise."""
        return tuple(self._under(p) for p in ('runs', 'leases', 'artifacts', 'serve')) if self.serves else ()

    @property
    def owned(self) -> tuple[str, ...]:
        """Every top-level directory this account may hold under its root; anything else there is a stray."""
        return self.shared + self.serving


def account_paths(root: str, *, serves: bool, account: str = '') -> AccountPaths:
    """The layout under the declared *root*; an undeclared or relative root is refused naming where to declare it."""
    if not root:
        msg = (
            f'{account or "this account"}: no root declared -- declare [hpc.clusters.<cluster>] root (or the '
            "account file's [deploy] root) in the family config, on a filesystem the compute nodes see"
        )
        raise RootUndeclared(msg)
    if not (posixpath.isabs(root) or ntpath.isabs(root)):  # a cluster is POSIX; a test's tmp may be a drive
        msg = f'{account or "this account"}: root {root!r} must be an absolute path (a declared root, never ~)'
        raise RootUndeclared(msg)
    return AccountPaths(posixpath.normpath(root), serves)


def export_line(paths: AccountPaths) -> str:
    """The one login-profile line that carries the root to every login shell and job of the account."""
    return f'export {ROOT_VAR}={shlex.quote(paths.root)}'


def local_root(env: Mapping[str, str] | None = None) -> AccountPaths:
    """The layout of the account this runs on, from ``LAB_ROOT`` (its deploy sets it); unset is refused by name."""
    root = (os.environ if env is None else env).get(ROOT_VAR, '')
    if not root:
        msg = (
            f'{ROOT_VAR} is unset: this account was not deployed with its declared root -- '
            'run python -m dev_lab.dev.hpcaccount deploy-fleet --account <user>'
        )
        raise RootUndeclared(msg)
    return account_paths(root, serves=True)
