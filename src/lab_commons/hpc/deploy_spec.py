"""The cluster deployment's DATA: the ``[deploy]`` declaration, and the crontab lines derived from it.

Pure: parse, render, derive. :mod:`lab_commons.hpc.deploy` runs the steps.
"""

from __future__ import annotations

import argparse
import shlex
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from lab_commons.hpc.retired import RETIRED

__all__ = [
    'DEFAULT_PYTHON',
    'DEFAULT_ROOT',
    'LAB_COMMONS',
    'MARK',
    'Deployment',
    'Repo',
    'checkout_of',
    'cron_lines',
    'has_grant',
    'load',
    'merge_crontab',
    'parse_repo',
    'render',
    'render_grant',
    'tick_command',
    'tick_dirs',
]

#: Every crontab line this tool owns ends with this comment plus the repo's name.
MARK: Final = '# lab-commons-deploy:'

DEFAULT_ROOT: Final = 'src'
DEFAULT_PYTHON: Final = '3.13'

#: The served repo every tick RUNS: its checkout is installed into every other served venv.
LAB_COMMONS: Final = 'lab-commons'

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
    """The ``[deploy]`` table of the machine file; the last two declare this account's grant."""

    host: str
    root: str
    python: str
    repos: tuple[Repo, ...]
    slurm_account: str = ''  # needed only when sacctmgr names several
    partitions: tuple[str, ...] = ()


def load(path: Path) -> Deployment | None:
    """The ``[deploy]`` table of the machine file at *path*; ``None`` when undeclared, a missing key refused."""
    raw = tomllib.loads(path.read_text(encoding='utf-8')).get('deploy') if path.is_file() else None
    if not raw:
        return None
    if retired := [
        f'[deploy] {key} is retired: {RETIRED[f"[deploy] {key}"]}' for key in raw if f'[deploy] {key}' in RETIRED
    ]:
        msg = f'{path}: ' + '; '.join(retired)
        raise ValueError(msg)
    try:
        return Deployment(
            host=raw['host'],
            root=raw.get('root', DEFAULT_ROOT),
            python=raw.get('python', DEFAULT_PYTHON),
            repos=tuple(Repo(r['name'], r['url'], r['ref']) for r in raw['repos']),
            slurm_account=raw.get('slurm_account', ''),
            partitions=tuple(raw.get('partitions', ())),
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
    account = f'slurm_account = {_toml(spec.slurm_account)}\n' if spec.slurm_account else ''
    return (
        f'\n[deploy]\nhost = {_toml(spec.host)}\nroot = {_toml(spec.root)}\npython = {_toml(spec.python)}\n'
        f'{account}partitions = {_list(spec.partitions)}\nrepos = [\n{repos}]\n'
    )


def _list(values: Sequence[str]) -> str:
    return '[' + ', '.join(_toml(v) for v in values) + ']'


def render_grant(grant: Mapping[str, Any]) -> str:
    """*grant* as the ``[[hpc.grant]]`` table :mod:`lab_commons.hpc.grants` reads."""
    rows = ''.join(
        f'{key} = {_list(value) if isinstance(value, list) else value if isinstance(value, int) else _toml(value)}\n'
        for key, value in grant.items()
    )
    return f'\n[[hpc.grant]]\n{rows}'


def has_grant(path: Path, user: str) -> bool:
    """Whether the machine file at *path* already grants *user* a share."""
    raw = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return any(grant.get('user') == user for grant in raw.get('hpc', {}).get('grant', []))


def parse_repo(text: str) -> Repo:
    """A ``--repo <name>=<https url>@<branch>`` argument."""
    name, _, rest = text.partition('=')
    url, _, ref = rest.rpartition('@')
    if not (name and url.startswith('https://') and ref):
        msg = f'--repo {text!r}: expected <name>=<https url>@<branch>'
        raise argparse.ArgumentTypeError(msg)
    return Repo(name, url, ref)


def cron_lines(spec: Deployment, home: Path, *, slurm: Sequence[str] = ()) -> list[str]:
    """One marked line per served repo: every 5 min, ``flock -n`` so a host never overlaps itself."""
    return [
        f'*/5 * * * * {tick_command(spec, home, repo, log=True, slurm=slurm)} {MARK}{repo.name}' for repo in spec.repos
    ]


def merge_crontab(current: str, lines: Sequence[str]) -> str:
    """*current* without any line this tool marked, plus *lines*; every other line kept in place."""
    kept = [line for line in current.splitlines() if MARK not in line]
    text = '\n'.join([*kept, *lines])
    return text + '\n' if text else ''


def tick_command(spec: Deployment, home: Path, repo: Repo, *, log: bool, slurm: Sequence[str] = ()) -> str:
    """The shell command of one tick of *repo*: cron PATH, its checkout, ``flock -n``, ``forge serve``."""
    path = ':'.join(tick_dirs(home, slurm))
    cache = home / '.cache'
    command = (
        f'PATH={path}; cd {shlex.quote(str(checkout_of(spec, home, repo)))} && '
        f'flock -n {shlex.quote(str(cache / f"lab-forge-{repo.name}.lock"))} '
        f'{_VENV_PYTHON} -m lab_commons.dev.forge serve'
    )
    return command + (f' >> {shlex.quote(str(cache / f"lab-forge-{repo.name}.log"))} 2>&1' if log else '')


def tick_dirs(home: Path, slurm: Sequence[str] = ()) -> list[str]:
    """The directories of a tick's PATH, in order: cron's own PATH is not the login shell's.

    *slurm* is where ``sbatch``, ``sacct`` and ``squeue`` resolved when the deploy ran -- a site install
    (``/opt/slurm/<version>/bin``) that cron's PATH never names.
    """
    base = [str(home / '.local/bin'), str(home / '.cargo/bin'), '/usr/local/bin', '/usr/bin', '/bin']
    return [*base, *(d for d in dict.fromkeys(slurm) if d not in base)]


def checkout_of(spec: Deployment, home: Path, repo: Repo) -> Path:
    """Where *repo* is checked out: ``~/<root>/<name>``."""
    return home / spec.root / repo.name
