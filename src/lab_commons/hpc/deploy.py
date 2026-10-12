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
import shlex
import shutil
import socket
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from lab_commons.config import config_path
from lab_commons.hpc.builds import Builds, key_of, read_builds
from lab_commons.log import emit

__all__ = [
    'MARK',
    'Deployment',
    'Repo',
    'Tools',
    'cmake_needed',
    'cron_lines',
    'deploy',
    'load',
    'merge_crontab',
    'render',
]

#: Every crontab line this tool owns ends with this comment plus the repo's name.
MARK: Final = '# lab-commons-deploy:'

_CREDENTIAL_REMEDY: Final = (
    'store your OWN forge token for it once: `git config --global credential.helper store`, then '
    '`python -m lab_commons.dev.forge auth login` (or one line https://<user>:<token>@<forge host> in '
    '~/.git-credentials, chmod 600) and re-run'
)

_which = shutil.which

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


@dataclass(frozen=True)
class Tools:
    """The argv prefix of every program the deploy runs; a test swaps in fakes."""

    git: tuple[str, ...] = ('git',)
    crontab: tuple[str, ...] = ('crontab',)
    uv: tuple[str, ...] = ('uv',)
    sh: tuple[str, ...] = ('bash', '-lc')


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


def _repo(text: str) -> Repo:
    name, _, rest = text.partition('=')
    url, _, ref = rest.rpartition('@')
    if not (name and url.startswith('https://') and ref):
        msg = f'--repo {text!r}: expected <name>=<https url>@<branch>'
        raise argparse.ArgumentTypeError(msg)
    return Repo(name, url, ref)


def cron_lines(spec: Deployment, home: Path) -> list[str]:
    """One marked line per served repo: every 5 min, ``flock -n`` so a host never overlaps itself."""
    return [f'*/5 * * * * {_tick_command(spec, home, repo, log=True)} {MARK}{repo.name}' for repo in spec.repos]


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


def _tick_command(spec: Deployment, home: Path, repo: Repo, *, log: bool) -> str:
    path = ':'.join([str(home / '.local/bin'), str(home / '.cargo/bin'), '/usr/local/bin', '/usr/bin', '/bin'])
    cache = home / '.cache'
    serve = ' '.join(shlex.quote(a) for a in spec.serve)
    command = (
        f'PATH={path}; cd {shlex.quote(str(_checkout(spec, home, repo)))} && '
        f'flock -n {shlex.quote(str(cache / f"lab-forge-{repo.name}.lock"))} '
        f'{_VENV_PYTHON} -m lab_commons.dev.forge serve {serve}'
    )
    return command + (f' >> {shlex.quote(str(cache / f"lab-forge-{repo.name}.log"))} 2>&1' if log else '')


def _checkout(spec: Deployment, home: Path, repo: Repo) -> Path:
    return home / spec.root / repo.name


class _Step:
    """Runs or, under ``--dry-run``, only prints each changing action; read-only calls always run."""

    def __init__(self, tools: Tools, *, dry: bool) -> None:
        self.tools, self.dry = tools, dry

    def read(self, argv: Sequence[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding='utf-8', check=False)

    def change(
        self,
        what: str,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        if self.dry:
            emit(f'  [dry-run] {what}')
            return 0
        emit(f'  {what}')
        done = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            cwd=cwd,
            env={**os.environ, **(env or {})},
        )
        for line in (done.stdout + done.stderr).strip().splitlines()[-20:]:
            emit(f'    | {line}')
        return done.returncode


def _key(step: _Step, checkout: Path, paths: Sequence[str], *recipe: str) -> str:
    oids = {}
    for path in paths:
        done = step.read([*step.tools.git, '-C', str(checkout), 'rev-parse', '--verify', '-q', f'HEAD:{path}'])
        oids[path] = done.stdout.strip() if done.returncode == 0 else 'absent'
    return key_of(oids, *recipe)


def _declared(checkout: Path) -> Builds:
    pyproject = checkout / 'pyproject.toml'
    return read_builds(pyproject.read_text(encoding='utf-8')) if pyproject.is_file() else Builds()


def _stamped(path: Path, key: str) -> bool:
    return path.is_file() and path.read_text(encoding='utf-8') == key


def _build(step: _Step, spec: Deployment, home: Path, repo: Repo) -> int:
    checkout = _checkout(spec, home, repo)
    if step.dry:
        step.change(f'{repo.name}: build .venv with its [tool.lab_commons.hpc] install (e.g. uv sync --extra dev)', [])
        step.change(f'{repo.name}: its native build, when declared, into {checkout / ".lab-native"}', [])
        return 0
    builds = _declared(checkout)
    if not builds.install:
        emit(f'  {repo.name} declares no [tool.lab_commons.hpc] install; refused', err=True)
        return 2
    env = {'UV_PYTHON': spec.python, 'UV_PYTHON_PREFERENCE': 'only-managed', 'UV_CACHE_DIR': str(home / 'ci/uv-cache')}
    inputs = builds.dependency_inputs or ('pyproject.toml', 'uv.lock')
    env_key = _key(step, checkout, inputs, builds.install, spec.python, *builds.needs)
    stamp = checkout / '.venv' / '.lab-deploy-env'
    if _stamped(stamp, env_key):
        emit(f'  {repo.name}: .venv current (key {env_key[:12]})')
    else:
        if rc := step.change(f'{repo.name}: {builds.install}', [*step.tools.sh, builds.install], cwd=checkout, env=env):
            return rc
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(env_key, encoding='utf-8')
    return _native(step, home, checkout, builds, {**env, 'ENV_KEY': env_key}) if builds.native_build else 0


def _native(step: _Step, home: Path, checkout: Path, builds: Builds, env: dict[str, str]) -> int:
    """The repo's native build into ``<checkout>/.lab-native``, sharing the cluster runs' cargo target."""
    key = _key(step, checkout, builds.native_inputs, builds.native_build, env['UV_PYTHON'], env.pop('ENV_KEY'))
    out = checkout / '.lab-native'
    stamp = out / '.lab-deploy-native'
    if _stamped(stamp, key):
        emit(f'  {checkout.name}: native build current (key {key[:12]})')
        return 0
    script = f'mkdir -p "$CARGO_TARGET_DIR" && exec 8> "$CARGO_TARGET_DIR.lock" && flock 8 && {builds.native_build}'
    env |= {'LAB_CI_NATIVE': str(out), 'CARGO_TARGET_DIR': str(home / 'ci/cargo-target')}
    if rc := step.change(
        f'{checkout.name}: native {builds.native_build}', [*step.tools.sh, script], cwd=checkout, env=env
    ):
        return rc
    out.mkdir(parents=True, exist_ok=True)
    stamp.write_text(key, encoding='utf-8')
    return 0


def _update(step: _Step, spec: Deployment, home: Path, repo: Repo) -> int:
    checkout, git = _checkout(spec, home, repo), step.tools.git
    if not (checkout / '.git').exists():
        argv = [*git, 'clone', '-q', '-b', repo.ref, repo.url, str(checkout)]
        return step.change(f'git clone -b {repo.ref} {repo.url} {checkout}', argv)
    remote = f'refs/remotes/origin/{repo.ref}'
    rc = step.change(
        f'{repo.name}: git fetch origin {repo.ref}',
        [*git, '-C', str(checkout), 'fetch', '-q', 'origin', f'+refs/heads/{repo.ref}:{remote}'],
    )
    return rc or step.change(
        f'{repo.name}: git checkout -B {repo.ref} origin/{repo.ref}',
        [*git, '-C', str(checkout), 'checkout', '-q', '-B', repo.ref, remote],
    )


def _crontab(step: _Step, lines: Sequence[str]) -> int:
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
    parser.add_argument('--repo', type=_repo, action='append', default=[], help='declare: <name>=<https url>@<branch>')
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


def _declare(step: _Step, spec: Deployment, path: Path) -> int:
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


def _credentials(step: _Step, spec: Deployment) -> int:
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


def _each(step: _Step, spec: Deployment, home: Path, title: str, act: Callable[..., int]) -> int:
    emit(title)
    for repo in spec.repos:
        if rc := act(step, spec, home, repo):
            return rc
    return 0


def _cmake(step: _Step, spec: Deployment, home: Path) -> int:
    emit('[tools] cmake, when a repo needs it')
    declared = [] if step.dry else [(r.name, _declared(_checkout(spec, home, r)).needs) for r in spec.repos]
    wanting = cmake_needed(declared, which=_which)
    if not wanting:
        return 0
    return step.change(
        f'{", ".join(wanting)} need cmake: uv tool install cmake', [*step.tools.uv, 'tool', 'install', 'cmake']
    )


def _cron(step: _Step, spec: Deployment, home: Path) -> int:
    emit('[cron] one marked line per served repo')
    if not step.dry:
        (home / '.cache').mkdir(parents=True, exist_ok=True)
    rc = _crontab(step, cron_lines(spec, home))
    for line in cron_lines(spec, home):
        emit(f'  {line}')
    return rc


def _tick(step: _Step, spec: Deployment, home: Path, repo: Repo) -> int:
    command = _tick_command(spec, home, repo, log=False)
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
    step = _Step(tools or Tools(), dry=args.dry_run)
    if args.remove:
        emit('[cron] removing the marked lines')
        return _crontab(step, [])
    phases = (
        lambda: _credentials(step, spec),
        lambda: _declare(step, spec, path),
        lambda: _each(step, spec, home, f'[checkouts] under ~/{spec.root}', _update),
        lambda: _cmake(step, spec, home),
        lambda: _each(step, spec, home, f'[venvs] python {spec.python} (uv-managed)', _build),
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
