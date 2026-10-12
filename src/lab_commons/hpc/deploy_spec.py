"""The cluster deployment's DATA: the ``[deploy]`` declaration, and the crontab lines derived from it.

Pure: parse, render, derive. :mod:`lab_commons.hpc.deploy` runs the steps.
"""

from __future__ import annotations

import argparse
import shlex
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

__all__ = [
    'DEFAULT_PYTHON',
    'DEFAULT_ROOT',
    'DEFAULT_SERVE',
    'MARK',
    'Deployment',
    'Repo',
    'checkout_of',
    'cmake_needed',
    'cron_lines',
    'load',
    'merge_crontab',
    'parse_repo',
    'render',
    'tick_command',
]

#: Every crontab line this tool owns ends with this comment plus the repo's name.
MARK: Final = '# lab-commons-deploy:'

DEFAULT_ROOT: Final = 'src'
DEFAULT_PYTHON: Final = '3.13'
DEFAULT_SERVE: Final = ('--mode', 'integrate')

#: The interpreter a cron line runs: the line is a Linux login node's, so the POSIX venv layout is the only one.
_VENV_PYTHON: Final = str(PurePosixPath('.venv', 'bin', 'python'))


@dataclass(frozen=True)
class Repo:
    """One served repository: its checkout name, HTTPS remote and the branch the checkout follows."""

    name: str
    url: str
    ref: str


@dataclass(frozen=True)
class Deployment:
    """The ``[deploy]`` table of the machine file."""

    host: str
    root: str
    python: str
    serve: tuple[str, ...]
    repos: tuple[Repo, ...]


def load(path: Path) -> Deployment | None:
    """The ``[deploy]`` table of the machine file at *path*; ``None`` when undeclared, a missing key refused."""
    raw = tomllib.loads(path.read_text(encoding='utf-8')).get('deploy') if path.is_file() else None
    if not raw:
        return None
    try:
        return Deployment(
            host=raw['host'],
            root=raw.get('root', DEFAULT_ROOT),
            python=raw.get('python', DEFAULT_PYTHON),
            serve=tuple(raw.get('serve', DEFAULT_SERVE)),
            repos=tuple(Repo(r['name'], r['url'], r['ref']) for r in raw['repos']),
        )
    except KeyError as missing:
        msg = f'[deploy] in {path} lacks {missing}'
        raise ValueError(msg) from None


def _toml(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render(spec: Deployment) -> str:
    """*spec* as the ``[deploy]`` TOML table :func:`load` reads back."""
    repos = ''.join(
        f'  {{ name = {_toml(r.name)}, url = {_toml(r.url)}, ref = {_toml(r.ref)} }},\n' for r in spec.repos
    )
    serve = ', '.join(_toml(a) for a in spec.serve)
    return (
        f'\n[deploy]\nhost = {_toml(spec.host)}\nroot = {_toml(spec.root)}\npython = {_toml(spec.python)}\n'
        f'serve = [{serve}]\nrepos = [\n{repos}]\n'
    )


def parse_repo(text: str) -> Repo:
    """A ``--repo <name>=<https url>@<branch>`` argument."""
    name, _, rest = text.partition('=')
    url, _, ref = rest.rpartition('@')
    if not (name and url.startswith('https://') and ref):
        msg = f'--repo {text!r}: expected <name>=<https url>@<branch>'
        raise argparse.ArgumentTypeError(msg)
    return Repo(name, url, ref)


def cron_lines(spec: Deployment, home: Path) -> list[str]:
    """One marked line per served repo: every 5 min, ``flock -n`` so a host never overlaps itself."""
    return [f'*/5 * * * * {tick_command(spec, home, repo, log=True)} {MARK}{repo.name}' for repo in spec.repos]


def merge_crontab(current: str, lines: Sequence[str]) -> str:
    """*current* without any line this tool marked, plus *lines*; every other line kept in place."""
    kept = [line for line in current.splitlines() if MARK not in line]
    text = '\n'.join([*kept, *lines])
    return text + '\n' if text else ''


def cmake_needed(declared: Sequence[tuple[str, Sequence[str]]], *, which: Callable[[str], str | None]) -> list[str]:
    """The repos whose ``needs`` name ``cmake`` when the host has none on PATH."""
    if which('cmake') is not None:
        return []
    return [name for name, needs in declared if 'cmake' in needs]


def tick_command(spec: Deployment, home: Path, repo: Repo, *, log: bool) -> str:
    """The shell command of one tick of *repo*: cron PATH, its checkout, ``flock -n``, ``forge serve``."""
    path = ':'.join([str(home / '.local/bin'), str(home / '.cargo/bin'), '/usr/local/bin', '/usr/bin', '/bin'])
    cache = home / '.cache'
    serve = ' '.join(shlex.quote(a) for a in spec.serve)
    command = (
        f'PATH={path}; cd {shlex.quote(str(checkout_of(spec, home, repo)))} && '
        f'flock -n {shlex.quote(str(cache / f"lab-forge-{repo.name}.lock"))} '
        f'{_VENV_PYTHON} -m lab_commons.dev.forge serve {serve}'
    )
    return command + (f' >> {shlex.quote(str(cache / f"lab-forge-{repo.name}.log"))} 2>&1' if log else '')


def checkout_of(spec: Deployment, home: Path, repo: Repo) -> Path:
    """Where *repo* is checked out: ``~/<root>/<name>``."""
    return home / spec.root / repo.name
