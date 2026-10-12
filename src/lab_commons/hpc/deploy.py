"""``python -m lab_commons.hpc deploy`` -- make this account's login host a ``forge serve`` host, in one run.

WHAT IS DEPLOYED IS DATA in this account's machine file (:mod:`lab_commons.config`, ``[deploy]``) -- never in
a tracked file, since the forge, its repos and the login host are private to the deployment::

    [deploy]
    host = "<login host>"          # the ONE host whose crontab serves this account (cron is per host)
    root = "src"                   # the served checkouts live in ~/<root>/<name>
    python = "3.13"                # a uv-managed CPython: manylinux wheels run on an old glibc
    serve = ["--mode", "integrate"]
    slurm_account = "<account>"    # only when sacctmgr names several
    partitions = ["<partition>", ...]
    repos = [{ name = "lab-commons", url = "<https url>", ref = "<branch>" }, { name = "<repo>", ... }, ...]

The first run DECLARES it with ``--host <host> --repo <name>=<url>@<ref> ... --partition <p> ...`` (written
once; a later run with flags that disagree is refused, naming the file); every later run reads it.
``lab-commons`` is always among the repos: every tick RUNS it, so the host serves it.

STEPS, each one reporting, each one safe to repeat: refuse off the declared host; check the account BEFORE
anything changes (:mod:`lab_commons.hpc.deploy_checks`: git's credential helper and its store, every repo's
``git ls-remote``, and the forge API calls a tick makes); write this account's own ``[[hpc.grant]]`` when
it has none; clone or update each checkout; install ``uv`` and every declared ``needs`` the tick's PATH
lacks, or refuse naming it; build each ``.venv`` with the repo's own ``[tool.lab_commons.hpc] install``
(and its ``native`` build), skipped when the key of its inputs is unchanged, then install the served
lab-commons checkout over the sha the repo pins -- keyed by that checkout's HEAD, so a lab-commons update
reinstalls it everywhere; replace this tool's marked crontab lines (one per repo, every 5 min, under
``flock -n``) and touch no other line; run one tick of each and print it. ``--dry-run`` reads but changes
nothing; ``--remove`` deletes the marked lines only. No credential is ever printed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from lab_commons.config import config_path
from lab_commons.hpc.deploy_checks import grant_of, store_problem, tool_plan
from lab_commons.hpc.deploy_spec import (
    DEFAULT_PYTHON,
    DEFAULT_ROOT,
    DEFAULT_SERVE,
    LAB_COMMONS,
    Deployment,
    Repo,
    checkout_of,
    cron_lines,
    has_grant,
    load,
    merge_crontab,
    parse_repo,
    render,
    render_grant,
    tick_command,
    tick_dirs,
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

#: Reads the forge API a tick needs for one repo url; ``''`` when nothing is refused.
ApiCheck = Callable[[str, Path], str]


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
    parser.add_argument('--slurm-account', default='', help="declare: the grant's account, when Slurm names several")
    parser.add_argument('--partition', action='append', default=[], help='declare: a partition the grant may use')
    parser.add_argument('--dry-run', action='store_true', help='print every action; change nothing')
    parser.add_argument('--remove', action='store_true', help='delete the marked crontab lines only')
    return parser


def _declared(args: argparse.Namespace, path: Path) -> Deployment | str:
    """The declared deployment (stored, else from the flags); a refusal is returned as its message."""
    stored = load(path)
    if not (args.host or args.repo):
        return stored or (
            f'no [deploy] in {path}: declare it once with --host <login host> --repo <name>=<https url>@<branch> ...'
        )
    if not (args.host and args.repo):
        return 'a declaration needs both --host and at least one --repo'
    given = Deployment(
        args.host, args.root, args.python, DEFAULT_SERVE, tuple(args.repo), args.slurm_account, tuple(args.partition)
    )
    if stored is not None and stored != given:
        return f'{path} already declares a different [deploy]; edit it there, or run without --host/--repo'
    return given


def _resolve(args: argparse.Namespace, path: Path) -> Deployment | str:
    """The declared deployment, refused unless it serves lab-commons -- the code every tick runs."""
    spec = _declared(args, path)
    if isinstance(spec, Deployment) and LAB_COMMONS not in {repo.name for repo in spec.repos}:
        return f'every tick runs lab-commons, so the host serves it: add --repo {LAB_COMMONS}=<https url>@<branch>'
    return spec


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


def _store_mode(store: Path) -> int | None:
    if not store.is_file():
        return None
    return stat.S_IMODE(store.stat().st_mode) if os.name == 'posix' else 0o600  # modes are POSIX's


def _credentials(step: Step, spec: Deployment, home: Path, api: ApiCheck) -> int:
    emit('[credentials] git credential helper, git ls-remote and the forge API, per served repo')
    helper = step.read([*step.tools.git, 'config', '--global', '--get', 'credential.helper']).stdout.strip()
    store = home / '.git-credentials'
    if problem := store_problem(helper, store, mode=_store_mode(store)):
        emit(f'deploy refused: {problem}', err=True)
        return 2
    denied = []
    for repo in spec.repos:
        rc = step.read([*step.tools.git, 'ls-remote', repo.url, f'refs/heads/{repo.ref}']).returncode
        emit(f'  {repo.name}: {"ok" if rc == 0 else f"refused (exit {rc})"}')
        if rc:
            denied.append(repo.name)
    if denied:
        emit(f'deploy refused: no read access to {", ".join(denied)}; {_CREDENTIAL_REMEDY}', err=True)
        return 2
    refused = {repo.name: why for repo in spec.repos if (why := api(repo.url, home))}
    for name, why in refused.items():
        emit(f'  {name}: {why}', err=True)
    if refused:
        emit(
            f'deploy refused: the forge API refuses what a tick needs on {", ".join(refused)}; store a token '
            'with the scopes each refusal names: `python -m lab_commons.dev.forge auth login`',
            err=True,
        )
        return 2
    return 0


def _auth_check(url: str, home: Path) -> str:
    """The forge's own ``auth check`` of *url*, run as a subprocess: :mod:`lab_commons.hpc` imports no dev code."""
    argv = [sys.executable, '-m', 'lab_commons.dev.forge', 'auth', 'check', '--url', url]
    done = subprocess.run(
        argv, cwd=home, capture_output=True, text=True, encoding='utf-8', errors='replace', check=False
    )
    return '' if done.returncode == 0 else done.stderr.strip() or f'auth check exited {done.returncode}'


def _grant(step: Step, spec: Deployment, path: Path, hostname: str) -> int:
    """Write this account's own ``[[hpc.grant]]`` when the machine file has none for it."""
    emit("[grant] this account's own [[hpc.grant]]")
    user = step.read([*step.tools.id, '-un']).stdout.strip()
    if has_grant(path, user):
        emit(f'  {user} already holds a grant in {path}')
        return 0
    sacct = step.read([*step.tools.sacctmgr, '-nP', 'show', 'assoc', f'user={user}', 'format=account,grptres'])
    grant = grant_of(user, sacct.stdout, spec=spec, host=hostname)
    if isinstance(grant, str):
        emit(f'deploy refused: {grant}', err=True)
        return 2
    text = render_grant(grant)
    if step.dry:
        emit(f'  [dry-run] write to {path}:{text}')
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as out:
        out.write(text)
    emit(f'  wrote the grant of {user} ({grant["slurm_account"]}, {grant["cpus"]} cpus) to {path}')
    return 0


def _each(step: Step, spec: Deployment, home: Path, title: str, act: Callable[..., int]) -> int:
    emit(title)
    for repo in spec.repos:
        if rc := act(step, spec, home, repo):
            return rc
    return 0


#: The Slurm commands a tick runs: it submits, then gathers and watches.
SLURM_TOOLS: Final = ('sbatch', 'sacct', 'squeue')


def _slurm_dirs() -> list[str] | str:
    """The directories ``sbatch``, ``sacct`` and ``squeue`` resolve in NOW; a missing one is refused by name."""
    found = {tool: _which(tool) for tool in SLURM_TOOLS}
    if missing := [tool for tool, where in found.items() if where is None]:
        return (
            f'{", ".join(missing)} not found on this PATH: a tick submits and gathers through Slurm. '
            'Run the deploy from a login shell where `command -v sbatch` answers'
        )
    return [str(Path(where).parent) for where in found.values() if where is not None]


def _tools(step: Step, spec: Deployment, home: Path, slurm: Sequence[str]) -> int:
    emit("[tools] uv and every declared `needs`, on the tick's PATH")
    needs = [] if step.dry else [need for r in spec.repos for need in declared(checkout_of(spec, home, r)).needs]
    path = os.pathsep.join(tick_dirs(home, slurm))
    installs, refused = tool_plan(needs, found=lambda tool: _which(tool, path=path) is not None)
    if refused:
        emit(
            f'deploy refused: {", ".join(refused)} not on the tick PATH ({":".join(tick_dirs(home, slurm))}) and not '
            'installable here; ask for it on the login node, or link it into ~/.local/bin, and re-run',
            err=True,
        )
        return 2
    for tool, script in installs:
        if rc := step.change(f'install {tool}: {script}', [*step.tools.sh, script]):
            return rc
    return 0


def _cron(step: Step, spec: Deployment, home: Path, slurm: Sequence[str]) -> int:
    emit('[cron] one marked line per served repo')
    if not step.dry:
        (home / '.cache').mkdir(parents=True, exist_ok=True)
    lines = cron_lines(spec, home, slurm=slurm)
    rc = _crontab(step, lines)
    for line in lines:
        emit(f'  {line}')
    return rc


def _tick(step: Step, spec: Deployment, home: Path, repo: Repo, slurm: Sequence[str] = ()) -> int:
    command = tick_command(spec, home, repo, log=False, slurm=slurm)
    rc = step.change(f'tick {repo.name}: {command}', [*step.tools.sh, command])
    if not step.dry:
        busy = ' (1 also means a tick of this host is still running)' if rc == 1 else ''
        emit(f'  tick {repo.name} exited {rc}{busy}')
    return 0  # a red tick is reported, never a reason to undo the deployment


def deploy(
    argv: Sequence[str], *, home: Path, hostname: str, tools: Tools | None = None, api: ApiCheck | None = None
) -> int:
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
    path_env = os.pathsep.join([*tick_dirs(home)[:2], os.environ.get('PATH', '')])
    step = Step(tools or Tools(), dry=args.dry_run, path=path_env)
    if args.remove:
        emit('[cron] removing the marked lines')
        return _crontab(step, [])
    check = api or _auth_check
    slurm = _slurm_dirs()
    if isinstance(slurm, str):
        emit(f'deploy refused: {slurm}', err=True)
        return 2
    phases = (
        lambda: _credentials(step, spec, home, check),
        lambda: _grant(step, spec, path, hostname),
        lambda: _declare(step, spec, path),
        lambda: _each(step, spec, home, f'[checkouts] under ~/{spec.root}', update),
        lambda: _tools(step, spec, home, slurm),
        lambda: _each(step, spec, home, f'[venvs] python {spec.python} (uv-managed)', build),
        lambda: _cron(step, spec, home, slurm),
        lambda: _each(step, spec, home, '[tick] one now, per repo', lambda *a: _tick(*a, slurm)),
    )
    for phase in phases:
        if rc := phase():
            return rc
    return 0


def main(argv: Sequence[str]) -> int:
    """The CLI entry: this box's home and its fully qualified name (the grant's ssh host)."""
    return deploy(argv, home=Path.home(), hostname=socket.getfqdn())
