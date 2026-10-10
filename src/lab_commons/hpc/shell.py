"""What a remote verdict RUNS ON THE CLUSTER: the spec, the shell it sends, and the parsing of what comes back.

Every script here is a plain string handed to a :data:`lab_commons.hpc.run.Runner`; nothing in this module
runs anything except :func:`pack`, which only READS the caller's repository. Paths stay under ``~/ci``.
"""

from __future__ import annotations

import base64
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from lab_commons.hpc.builds import Builds

__all__ = [
    'BUILD_FAILED',
    'BUILD_LOG',
    'COLLECT_MODULE',
    'ENV',
    'ITEM_MODULE',
    'NEEDS',
    'PREP_LOG',
    'ROOT',
    'TIME',
    'VerdictSpec',
    'build_script',
    'collect_command',
    'facts',
    'fetch_script',
    'group_items',
    'needs_script',
    'pack',
    'parse_collection_errors',
    'parse_ids',
    'parse_times',
]

#: The item runner's name on the cluster -- unique, so it can shadow nothing in the tested project.
ITEM_MODULE: Final = 'lab_ci_pytest_item'

ROOT: Final = '"$HOME"/ci'

#: Where a tree's build output is kept on the cluster, relative to the tree.
BUILD_LOG: Final = '.lab-ci/build.log'

#: The line :func:`build_script` prints instead of the facts when the build failed.
BUILD_FAILED: Final = '@@@ build-failed'

#: Where each preparation step's wall time is appended, relative to the tree (one line per step and run).
PREP_LOG: Final = '.lab-ci/prep.log'

#: The prefix of a ``<step>=<seconds>s`` timing line -- printed AND appended to :data:`PREP_LOG`.
TIME: Final = '@@@ time'

#: Persistent caches shared by every key: uv's (concurrency-safe by its own lock) and cargo's target dir.
_UV_CACHE: Final = f'{ROOT}/uv-cache'
_CARGO_TARGET: Final = f'{ROOT}/cargo-target'

#: Lines of the build log a refusal quotes.
_LOG_TAIL: Final = 40

#: What every verdict needs on the login node, whatever it installs.
NEEDS: Final = ('git', 'uv')

#: Hex digits of a full SHA-1 commit id.
_SHA_HEX: Final = 40

_GIT: Final = shutil.which('git') or 'git'

#: Enter the tree's environment (venv, source roots, native build) -- written by :func:`build_script`.
ENV: Final = '. .lab-ci/env.sh'

#: The per-file collector's name on the cluster (:mod:`lab_commons.hpc.collect`).
COLLECT_MODULE: Final = 'lab_ci_collect'


@dataclass(frozen=True)
class VerdictSpec:
    """What to test and how to build it. Shell strings run on the cluster inside the tree."""

    sha: str
    repo_url: str
    install: str
    collect: str = ''
    select: str = ''
    table: dict[str, tuple[str, ...]] = field(default_factory=dict)
    python: str = ''
    pack_from: Path | None = None
    needs: tuple[str, ...] = ()
    builds: Builds = field(default_factory=Builds)
    only: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """A verdict is bound to a full commit id, never to a branch name that moves under it."""
        if len(self.sha) != _SHA_HEX or any(c not in '0123456789abcdef' for c in self.sha):
            msg = f'a verdict needs the full 40-hex commit id, got {self.sha!r}'
            raise ValueError(msg)

    @property
    def tree(self) -> str:
        """The worktree, ``~``-relative so the shell expands it."""
        return f'~/ci/trees/{self.sha}'


def fetch_script(spec: VerdictSpec) -> str:
    """Make the cache hold the commit -- remote heads, else a shipped pack indexed and named HERE; have/missing."""
    sha, url, cache = spec.sha, shlex.quote(spec.repo_url), f'{ROOT}/cache.git'
    return '\n'.join(
        [
            'set -eu',
            f'mkdir -p {ROOT}/trees {ROOT}/packs {ROOT}/bin {ROOT}/runs',
            f'[ -d {cache} ] || git init -q --bare {cache}',
            f'have() {{ git -C {cache} cat-file -e {sha}^{{commit}} 2>/dev/null; }}',
            f'have || git -C {cache} fetch -q {url} "+refs/heads/*:refs/remotes/origin/*"',
            f'have || git -C {cache} fetch -q {url} {sha} 2>/dev/null || true',
            f'P={ROOT}/packs/{sha}.pack',
            (
                f'have || {{ [ -s "$P" ] && git -C {cache} index-pack --stdin < "$P" > /dev/null'
                f' && git -C {cache} update-ref refs/lab-ci/{sha} {sha}; }} || true'
            ),
            'if have; then echo have; else echo missing; fi',
        ]
    )


def needs_script(spec: VerdictSpec) -> str:
    """Print ``@@@ missing <command>`` for every prerequisite the login node lacks: :data:`NEEDS` plus ``spec.needs``.

    Checked BEFORE anything is fetched or built, so a grant without ``cargo`` is named up front instead of
    being discovered by a failed build -- or, as measured 2026-10-09, by 276 dead shards.
    """
    wanted = ' '.join(shlex.quote(c) for c in dict.fromkeys((*NEEDS, *spec.needs)))
    return f'for c in {wanted}; do command -v "$c" > /dev/null 2>&1 || echo "@@@ missing $c"; done; true'


def _step(directory: str, log: str, build: str) -> list[str]:
    """Shell lines building *directory* once with *build* (its own ``bash -e``), under a lock, log kept at *log*."""
    return [
        f'D={directory}; L={log}',
        'if [ ! -f "$D/.lab-ci-installed" ]; then',
        '  mkdir -p "$D" "$(dirname "$L")"',
        '  exec 9> "$D.lock"; flock 9',
        '  if [ ! -f "$D/.lab-ci-installed" ]; then',
        f'    if ! bash -eo pipefail -c {shlex.quote(build)} > "$L" 2>&1; then',
        f'      echo "{BUILD_FAILED} log=$L"; tail -n {_LOG_TAIL} "$L"; exit 0',
        '    fi',
        '    touch "$D/.lab-ci-installed"',
        '  fi',
        '  exec 9>&-',
        'fi',
    ]


def _timed(step: str, lines: list[str]) -> list[str]:
    """*lines* then a :data:`TIME` line for *step*, printed and appended to :data:`PREP_LOG` (cwd: the tree)."""
    return [
        f'T_{step}=$(date +%s)',
        *lines,
        f'echo "{TIME} {step}=$(( $(date +%s) - T_{step} ))s" | tee -a {PREP_LOG}',
    ]


def parse_times(text: str) -> dict[str, int]:
    """``{step: seconds}`` from the :data:`TIME` lines of *text*."""
    times: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith(f'{TIME} ') and '=' in line:
            step, _, seconds = line[len(TIME) + 1 :].partition('=')
            times[step.strip()] = int(seconds.strip().rstrip('s'))
    return times


def build_script(spec: VerdictSpec) -> str:
    """Add the tree, build (or REUSE) its venv and native build, write its ``.lab-ci/env.sh``. Prints the facts.

    The item runner's source arrives on stdin, so the script's first line is a ``cat``. Declared inputs
    (:mod:`lab_commons.hpc.builds`) put the venv in ``~/ci/envs/<key>`` and the native build in
    ``~/ci/native/<key>``, each built once per key under a ``flock``; the tree's ``source_roots`` reach
    them through ``PYTHONPATH``. Undeclared, the venv is the tree's own ``.venv``. A FAILED BUILD STOPS THE
    VERDICT: each build runs in its own ``bash -e``, its output is kept in a log next to what it builds
    (:data:`BUILD_LOG` inside the tree for a tree's own venv), and a non-zero exit -- or no ``bin/python``
    after it -- prints :data:`BUILD_FAILED` and the log's tail instead of the facts. Measured 2026-10-09:
    shards were submitted with no venv in their tree and all 276 died on ``.venv/bin/activate``.

    Every env build shares ``UV_CACHE_DIR`` (:data:`_UV_CACHE`) and every native build ``CARGO_TARGET_DIR``
    (:data:`_CARGO_TARGET`, under its own ``flock``), so a new key re-links cached wheels and a Rust edit
    compiles only the crates it touched. Each step's wall time is a :data:`TIME` line (:data:`PREP_LOG`).
    """
    cache, tree, builds = f'{ROOT}/cache.git', f'{ROOT}/trees/{spec.sha}', spec.builds
    python = f' --python {shlex.quote(spec.python)}' if spec.python else ''
    if builds.env_key:
        env, env_log = f'{ROOT}/envs/{builds.env_key}', f'{ROOT}/envs/{builds.env_key}.log'
    else:
        env, env_log = f'{tree}/.venv', f'{tree}/{BUILD_LOG}'
    activate = 'export VIRTUAL_ENV="$E" PATH="$E/bin:$PATH" UV_PROJECT_ENVIRONMENT="$E"'
    lines = [
        'set -eo pipefail',
        f'cat > {ROOT}/bin/{ITEM_MODULE}.py',
        f'[ -d {tree} ] || git -C {cache} worktree add -q --detach {tree} {spec.sha}',
        f'cd {tree}',
        # a commit's tree INCLUDES its submodules at their pinned commits; without them the tests
        # that read a submodule's data fail as if the code were broken (1240 ids, measured 2026-10-10)
        '[ ! -f .gitmodules ] || git submodule update -q --init --recursive',
        'mkdir -p .lab-ci',
        f'export E={env} UV_CACHE_DIR={_UV_CACHE}',
        *_timed(
            'env',
            _step('"$E"', env_log, '\n'.join([f'uv venv -q --allow-existing{python} "$E"', activate, spec.install])),
        ),
        f'[ -x "$E/bin/python" ] || {{ echo "{BUILD_FAILED} no bin/python in $E after the build"; exit 0; }}',
    ]
    path = [f'{tree}/{root}' for root in builds.source_roots]
    if builds.native_key:
        native = f'{ROOT}/native/{builds.native_key}'
        cargo = f'export CARGO_TARGET_DIR={_CARGO_TARGET}\nmkdir -p "$CARGO_TARGET_DIR"'
        cargo += '\nexec 8> "$CARGO_TARGET_DIR.lock"; flock 8'
        build = f'{activate}\nexport LAB_CI_NATIVE="$N"\n{cargo}\n{builds.native_build}'
        lines += [f'export N={native}', *_timed('native', _step('"$N"', f'{native}.log', build))]
        path.append(native)
    pythonpath = ':'.join([*path, f'{ROOT}/bin']).replace('"$HOME"', '$HOME')
    lines += [
        'printf \'export VIRTUAL_ENV="%s" PATH="%s/bin:$PATH"\\n\' "$E" "$E" > .lab-ci/env.sh',
        f'echo "export PYTHONPATH=\\"{pythonpath}\\${{PYTHONPATH:+:\\$PYTHONPATH}}\\"" >> .lab-ci/env.sh',
        'echo "@@@ facts"',
        'echo "platform=$(uname -s | tr A-Z a-z)-$(uname -m)/glibc$(getconf GNU_LIBC_VERSION | cut -d" " -f2)"',
        f'echo "python=$({ENV} && python -c "import platform; print(platform.python_version())")"',
    ]
    return '\n'.join(lines)


def collect_command(spec: VerdictSpec, *, also: str = '') -> str:
    """Collect in the tree; ONE ``-m`` joins ``select`` and the marker expression *also*.

    One expression, because pytest keeps only the last ``-m`` -- a second one would silently drop the first.
    When ``collect`` is paths only, the per-file cache (:mod:`lab_commons.hpc.collect`) recollects only the
    files whose content changed; any option in it falls back to one plain ``pytest --collect-only``.
    """
    parts = [f'({spec.select})'] if spec.select else []
    if also:
        parts.append(f'({also})')
    select = f' -m {shlex.quote(" and ".join(parts))}' if parts else ''
    head = f'cd {ROOT}/trees/{spec.sha} && {ENV} && '
    if any(token.startswith('-') for token in shlex.split(spec.collect)):
        body = (
            f'python -m pytest --collect-only -q -p no:cacheprovider --color=no'
            f' --continue-on-collection-errors{select} {spec.collect}'
        ).rstrip() + ' || true'
    else:
        seed = shlex.quote(f'{spec.builds.env_key}:{spec.builds.native_key}:{spec.python}')
        body = (
            f'{{ python {ROOT}/bin/{COLLECT_MODULE}.py --cache {ROOT}/collect --key {seed}{select}'
            f' --out .lab-ci/collected.txt -- {spec.collect} || true; }}; cat .lab-ci/collected.txt'
        )
    return head + '; '.join(_timed('collect', [f'{{ {body}; }}']))


def parse_ids(text: str) -> list[str]:
    """Node ids from ``pytest --collect-only -q`` -- the lines that carry ``::``, in collection order."""
    return [line.strip() for line in text.splitlines() if '::' in line and not line.startswith((' ', 'ERROR '))]


def parse_collection_errors(text: str) -> list[str]:
    """Files (or node ids) that failed to COLLECT -- pytest's ``ERROR <path>[ - reason]`` summary lines.

    A file that cannot be imported on this platform is a result for that file, never a reason to abort the
    whole tree: it is recorded as ``error`` and every other file still runs.
    """
    return [line[len('ERROR ') :].split(' - ', 1)[0].strip() for line in text.splitlines() if line.startswith('ERROR ')]


def group_items(ids: Sequence[str]) -> list[list[str]]:
    """One item per test file, in first-seen order."""
    files: dict[str, list[str]] = {}
    for node in ids:
        files.setdefault(node.split('::')[0], []).append(node)
    return list(files.values())


def facts(text: str) -> dict[str, str]:
    """``key=value`` lines after ``@@@ facts``."""
    _, _, tail = text.partition('@@@ facts')
    return dict(line.split('=', 1) for line in tail.splitlines() if '=' in line)


def pack(repo: Path, sha: str) -> str:
    """A pack of *sha*'s objects minus what the repo's remote-tracking refs already hold, base64 for a text stdin.

    READ-ONLY, and that is the point: ``git bundle`` takes NAMED refs only, so the bundle this replaced
    wrote a temporary ``refs/lab-ci/<sha>`` into the caller's checkout. ``pack-objects --revs --stdout``
    takes the commit and the exclusions on stdin and writes nothing; the CLUSTER indexes the pack into its
    cache and names the ref there (:func:`fetch_script`).
    """
    git = [_GIT, '-C', str(repo)]
    remotes = subprocess.run(
        [*git, 'for-each-ref', '--format=%(objectname)', 'refs/remotes'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout.split()
    revs = '\n'.join([sha, *(f'^{oid}' for oid in remotes)]) + '\n'
    done = subprocess.run(
        [*git, 'pack-objects', '--revs', '--stdout', '-q'], input=revs.encode('ascii'), capture_output=True, check=True
    )
    return base64.b64encode(done.stdout).decode('ascii')
