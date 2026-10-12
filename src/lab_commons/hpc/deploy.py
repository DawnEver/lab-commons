"""``python -m lab_commons.hpc deploy`` -- make this account's login host a ``forge serve`` host, in one run.

WHAT IS DEPLOYED IS DATA in this account's machine file (:mod:`lab_commons.config`, ``[deploy]``) -- never in
a tracked file, since the forge, its repos and the login host are private to the deployment::

    [deploy]
    host = "<login host>"          # the ONE host whose crontab serves this account (cron is per host)
    root = "src"                   # the served checkouts live in ~/<root>/<name>
    python = "3.13"                # a uv-managed CPython: manylinux wheels run on an old glibc
    serve = ["--mode", "integrate"]
    repos = [{ name = "<repo>", url = "<https url>", ref = "<branch>" }, ...]

The first run DECLARES it with ``--host <host> --repo <name>=<url>@<ref> ...`` (written once; a later run
with flags that disagree is refused, naming the file); every later run reads it.

STEPS, each one reporting, each one safe to repeat: refuse off the declared host; check every repo's
credential with ``git ls-remote`` BEFORE anything changes; clone or update each checkout; build its
``.venv`` with the repo's own ``[tool.lab_commons.hpc] install`` (and its ``native`` build), skipped when
the key of its inputs is unchanged; install ``cmake`` through uv when a repo ``needs`` it and the host has
none; replace this tool's marked crontab lines (one per repo, every 5 min, under ``flock -n``) and touch no
other line; run one tick of each and print it. ``--dry-run`` reads but changes nothing; ``--remove``
deletes the marked lines only. No credential is ever printed: a failed ``ls-remote`` reports its exit code.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from lab_commons.config import config_path
from lab_commons.hpc.deploy_spec import (
    DEFAULT_PYTHON,
    DEFAULT_ROOT,
    DEFAULT_SERVE,
    Deployment,
    Repo,
    checkout_of,
    cmake_needed,
    cron_lines,
    load,
    merge_crontab,
    parse_repo,
    render,
    tick_command,
)
from lab_commons.hpc.deploy_steps import Step, Tools, build, declared, update
from lab_commons.log import emit

__all__ = ['deploy', 'main']


_CREDENTIAL_REMEDY: Final = (
    'store your OWN forge token for it once: `git config --global credential.helper store`, then '
    '`python -m lab_commons.dev.forge auth login` (or one line https://<user>:<token>@<forge host> in '
    '~/.git-credentials, chmod 600) and re-run'
)

_which = shutil.which


def _crontab(step: Step, lines: Sequence[str]) -> int:
    listed = step.read([*step.tools.crontab, '-l'])
    current = listed.stdout if listed.returncode == 0 else ''
    if listed.returncode and 'no crontab' not in listed.stderr:
        emit(f'  crontab -l exited {listed.returncode}: {listed.stderr.strip()}', err=True)
        return 2
    wanted = merge_crontab(current, lines)
    if wanted == current:
        emit(f'  crontab: the {len(lines)} marked line(s) are already in place')
        return 0
    return step.change(
        f'crontab: write {len(lines)} marked line(s), every other line kept', [*step.tools.crontab, '-'], stdin=wanted
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='lab_commons.hpc deploy', description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, help='the machine file holding [deploy] (default: lab_commons.config)')
    parser.add_argument('--host', help='declare: the one login host serving this account')
    parser.add_argument(
        '--repo', type=parse_repo, action='append', default=[], help='declare: <name>=<https url>@<branch>'
    )
    parser.add_argument('--root', default=DEFAULT_ROOT, help='declare: checkouts live in ~/<root>/<name>')
    parser.add_argument('--python', default=DEFAULT_PYTHON, help='declare: the uv-managed interpreter request')
    parser.add_argument('--dry-run', action='store_true', help='print every action; change nothing')
    parser.add_argument('--remove', action='store_true', help='delete the marked crontab lines only')
    return parser


def _resolve(args: argparse.Namespace, path: Path) -> Deployment | str:
    """The declared deployment (stored, else from the flags); a refusal is returned as its message."""
    stored = load(path)
    if not (args.host or args.repo):
        return stored or (
            f'no [deploy] in {path}: declare it once with --host <login host> --repo <name>=<https url>@<branch> ...'
        )
    if not (args.host and args.repo):
        return 'a declaration needs both --host and at least one --repo'
    given = Deployment(args.host, args.root, args.python, DEFAULT_SERVE, tuple(args.repo))
    if stored is not None and stored != given:
        return f'{path} already declares a different [deploy]; edit it there, or run without --host/--repo'
    return given


def _declare(step: Step, spec: Deployment, path: Path) -> int:
    """Store a first declaration once the host and every credential have passed."""
    if load(path) is not None:
        return 0
    emit('[declare]')
    if step.dry:
        emit(f'  [dry-run] declare [deploy] in {path}')
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as out:
        out.write(render(spec))
    emit(f'  declared [deploy] in {path}')
    return 0


def _credentials(step: Step, spec: Deployment) -> int:
    emit('[credentials] git ls-remote per served repo')
    denied = []
    for repo in spec.repos:
        rc = step.read([*step.tools.git, 'ls-remote', repo.url, f'refs/heads/{repo.ref}']).returncode
        emit(f'  {repo.name}: {"ok" if rc == 0 else f"refused (exit {rc})"}')
        if rc:
            denied.append(repo.name)
    if denied:
        emit(f'deploy refused: no read access to {", ".join(denied)}; {_CREDENTIAL_REMEDY}', err=True)
        return 2
    return 0


def _each(step: Step, spec: Deployment, home: Path, title: str, act: Callable[..., int]) -> int:
    emit(title)
    for repo in spec.repos:
        if rc := act(step, spec, home, repo):
            return rc
    return 0


def _cmake(step: Step, spec: Deployment, home: Path) -> int:
    emit('[tools] cmake, when a repo needs it')
    needs = [] if step.dry else [(r.name, declared(checkout_of(spec, home, r)).needs) for r in spec.repos]
    wanting = cmake_needed(needs, which=_which)
    if not wanting:
        return 0
    return step.change(
        f'{", ".join(wanting)} need cmake: uv tool install cmake', [*step.tools.uv, 'tool', 'install', 'cmake']
    )


def _cron(step: Step, spec: Deployment, home: Path) -> int:
    emit('[cron] one marked line per served repo')
    if not step.dry:
        (home / '.cache').mkdir(parents=True, exist_ok=True)
    rc = _crontab(step, cron_lines(spec, home))
    for line in cron_lines(spec, home):
        emit(f'  {line}')
    return rc


def _tick(step: Step, spec: Deployment, home: Path, repo: Repo) -> int:
    command = tick_command(spec, home, repo, log=False)
    rc = step.change(f'tick {repo.name}: {command}', [*step.tools.sh, command])
    if not step.dry:
        busy = ' (1 also means a tick of this host is still running)' if rc == 1 else ''
        emit(f'  tick {repo.name} exited {rc}{busy}')
    return 0  # a red tick is reported, never a reason to undo the deployment


def deploy(argv: Sequence[str], *, home: Path, hostname: str, tools: Tools | None = None) -> int:
    """Run the deployment *argv* asks for; 0 done, 2 refused (the message names the remedy)."""
    args = _parser().parse_args(list(argv))
    path = args.config or config_path()
    spec = _resolve(args, path)
    if isinstance(spec, str):
        emit(f'deploy refused: {spec}', err=True)
        return 2
    short = hostname.split('.', 1)[0]
    emit(f'[host] account {os.environ.get("USER") or os.environ.get("USERNAME", "?")} on {short}')
    if short != spec.host:
        emit(
            f'deploy refused: this account serves from {spec.host} ([deploy] host in '
            f'{path}); this is {short}. ssh to {spec.host} and run it there.',
            err=True,
        )
        return 2
    step = Step(tools or Tools(), dry=args.dry_run)
    if args.remove:
        emit('[cron] removing the marked lines')
        return _crontab(step, [])
    phases = (
        lambda: _credentials(step, spec),
        lambda: _declare(step, spec, path),
        lambda: _each(step, spec, home, f'[checkouts] under ~/{spec.root}', update),
        lambda: _cmake(step, spec, home),
        lambda: _each(step, spec, home, f'[venvs] python {spec.python} (uv-managed)', build),
        lambda: _cron(step, spec, home),
        lambda: _each(step, spec, home, '[tick] one now, per repo', _tick),
    )
    for phase in phases:
        if rc := phase():
            return rc
    return 0


def main(argv: Sequence[str]) -> int:
    """The CLI entry: this box's home and hostname."""
    return deploy(argv, home=Path.home(), hostname=socket.gethostname())
