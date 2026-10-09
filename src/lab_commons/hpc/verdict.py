"""A remote verdict: one commit's test suite, sharded over a cluster, gathered into a record bound to the commit.

EVERYTHING LIVES UNDER ``~/ci/`` ON THE CLUSTER, AND NOTHING ELSE IS TOUCHED. A day-to-day checkout in
``~/<repo>`` and its venv are someone's working state; a verdict must neither reinstall nor read them::

    ~/ci/cache.git              bare cache; fetched from the read-only HTTPS remote
    ~/ci/packs/<sha>.pack       only when the commit is not on the remote (sent over stdin, indexed here)
    ~/ci/runs/<sha>/state.json  the run: its rounds, outcomes so far and the job of the round in flight
    ~/ci/trees/<sha>/           a worktree of the cache, with its OWN .venv (reused when present)
    ~/ci/bin/lab_ci_pytest_item.py   the item runner, shipped from this checkout (pytest_item.py)

THE FLOW. Probe every grant and pick the cluster (:func:`lab_commons.hpc.plan.allocate`); fetch the
commit (or ship a pack); add the tree; build its venv on the login node with the caller's install
command; collect node ids there; group them by file into items; re-allocate the real item count among
the grants on that cluster; submit the array and leave the run's state on the cluster
(:func:`submit_verdict`, minutes). Then each :func:`gather_verdict` is ONE short call: still active is
pending; a finished round is folded, its unfinished ids re-submitted as the next round, or the record
returned. NOTHING RESIDENT RUNS ON THE CALLER'S BOX, and the caller's repository is only READ.

NOT COVERED IS NOT PASSED. The commit's own ``[tool.lab_commons.platforms]`` table
(:mod:`lab_commons.hpc.platforms`) says which markers each platform cannot run; an id the
assignment rule (:func:`lab_commons.hpc.platforms.assign`) gives to another part is recorded as
``not-covered`` and listed in the record's ``handed`` with the platforms that CAN run it (``[]`` when
none can). Admission composes it with the other parts; no part reads it to choose its ids. Only
``passed`` means passed.

A KILLED SHARD COSTS ONLY WHAT IT DID NOT FINISH. Every item streams its outcomes as they happen
(:mod:`lab_commons.hpc.pytest_item`); the verdict reads the streams, not the shards' end-of-run files.
An item whose stream never closed is unfinished: its unreported ids are re-submitted, split in halves,
for up to :data:`RETRIES` more rounds -- a round after an ``OUT_OF_MEMORY`` asks for twice the memory --
and only what is still unfinished after that is ``lost``. An item that closed without reporting an id
recorded it ``missing``.

THE PLAN IS MEASURED. The record keeps ``durations`` (seconds per id), ``overheads`` (per file: wall time
minus its tests', i.e. interpreter and imports) and ``peaks_mb`` (per file); the next verdict reads them
back (``history``), so an item's time is its file's overhead plus its ids' durations, the array's
memory is the worst measured peak times ``policy.safety``, and ``cost`` is the fallback only for a file
never measured -- named in the plan as ``unmeasured``.
"""

from __future__ import annotations

import base64
import json
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.builds import Builds
from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.measured import Measured
from lab_commons.hpc.plan import Plan, allocate
from lab_commons.hpc.platforms import LINUX, PLATFORMS, assign, cannot_run, runnable
from lab_commons.hpc.pytest_item import read_stream
from lab_commons.hpc.run import Runner, Unreachable, probe, shard_states, submit
from lab_commons.hpc.slurm import Snapshot

__all__ = [
    'ACTIVE',
    'BUILD_FAILED',
    'BUILD_LOG',
    'COLLECT_MODULE',
    'ITEM_MODULE',
    'NEEDS',
    'RETRIES',
    'VerdictSpec',
    'build_script',
    'collect_command',
    'fetch_script',
    'gather_verdict',
    'group_items',
    'needs_script',
    'parse_collection_errors',
    'parse_ids',
    'read_streams',
    'submit_verdict',
    'summary',
    'write_record',
]

#: Slurm states during which a shard may still write its results.
ACTIVE: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'CONFIGURING', 'COMPLETING', 'RESIZING', 'SUSPENDED'})

#: Rounds after the first that re-run what a killed shard left unfinished.
RETRIES: Final = 2

#: The item runner's name on the cluster -- unique, so it can shadow nothing in the tested project.
ITEM_MODULE: Final = 'lab_ci_pytest_item'

_ROOT: Final = '"$HOME"/ci'

#: Where a tree's build output is kept on the cluster, relative to the tree.
BUILD_LOG: Final = '.lab-ci/build.log'

#: The line :func:`build_script` prints instead of the facts when the build failed.
BUILD_FAILED: Final = '@@@ build-failed'

#: Lines of the build log a refusal quotes.
_LOG_TAIL: Final = 40

#: What every verdict needs on the login node, whatever it installs.
NEEDS: Final = ('git', 'uv')

#: Hex digits of a full SHA-1 commit id.
_SHA_HEX: Final = 40

_GIT: Final = shutil.which('git') or 'git'

#: Enter the tree's environment (venv, source roots, native build) -- written by :func:`build_script`.
_ENV: Final = '. .lab-ci/env.sh'

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
    sha, url, cache = spec.sha, shlex.quote(spec.repo_url), f'{_ROOT}/cache.git'
    return '\n'.join(
        [
            'set -eu',
            f'mkdir -p {_ROOT}/trees {_ROOT}/packs {_ROOT}/bin {_ROOT}/runs',
            f'[ -d {cache} ] || git init -q --bare {cache}',
            f'have() {{ git -C {cache} cat-file -e {sha}^{{commit}} 2>/dev/null; }}',
            f'have || git -C {cache} fetch -q {url} "+refs/heads/*:refs/remotes/origin/*"',
            f'have || git -C {cache} fetch -q {url} {sha} 2>/dev/null || true',
            f'P={_ROOT}/packs/{sha}.pack',
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
    """
    cache, tree, builds = f'{_ROOT}/cache.git', f'{_ROOT}/trees/{spec.sha}', spec.builds
    python = f' --python {shlex.quote(spec.python)}' if spec.python else ''
    if builds.env_key:
        env, env_log = f'{_ROOT}/envs/{builds.env_key}', f'{_ROOT}/envs/{builds.env_key}.log'
    else:
        env, env_log = f'{tree}/.venv', f'{tree}/{BUILD_LOG}'
    activate = 'export VIRTUAL_ENV="$E" PATH="$E/bin:$PATH" UV_PROJECT_ENVIRONMENT="$E"'
    lines = [
        'set -eo pipefail',
        f'cat > {_ROOT}/bin/{ITEM_MODULE}.py',
        f'[ -d {tree} ] || git -C {cache} worktree add -q --detach {tree} {spec.sha}',
        f'cd {tree}',
        'mkdir -p .lab-ci',
        f'export E={env}',
        *_step('"$E"', env_log, '\n'.join([f'uv venv -q --allow-existing{python} "$E"', activate, spec.install])),
        f'[ -x "$E/bin/python" ] || {{ echo "{BUILD_FAILED} no bin/python in $E after the build"; exit 0; }}',
    ]
    path = [f'{tree}/{root}' for root in builds.source_roots]
    if builds.native_key:
        native = f'{_ROOT}/native/{builds.native_key}'
        lines += [
            f'export N={native}',
            *_step('"$N"', f'{native}.log', f'{activate}\nexport LAB_CI_NATIVE="$N"\n{builds.native_build}'),
        ]
        path.append(native)
    pythonpath = ':'.join([*path, f'{_ROOT}/bin']).replace('"$HOME"', '$HOME')
    lines += [
        'printf \'export VIRTUAL_ENV="%s" PATH="%s/bin:$PATH"\\n\' "$E" "$E" > .lab-ci/env.sh',
        f'echo "export PYTHONPATH=\\"{pythonpath}\\${{PYTHONPATH:+:\\$PYTHONPATH}}\\"" >> .lab-ci/env.sh',
        'echo "@@@ facts"',
        'echo "platform=$(uname -s | tr A-Z a-z)-$(uname -m)/glibc$(getconf GNU_LIBC_VERSION | cut -d" " -f2)"',
        f'echo "python=$({_ENV} && python -c "import platform; print(platform.python_version())")"',
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
    head = f'cd {_ROOT}/trees/{spec.sha} && {_ENV} && '
    if any(token.startswith('-') for token in shlex.split(spec.collect)):
        return (
            f'{head}python -m pytest --collect-only -q -p no:cacheprovider --color=no'
            f' --continue-on-collection-errors{select} {spec.collect}'
        ).rstrip() + ' || true'
    seed = shlex.quote(f'{spec.builds.env_key}:{spec.builds.native_key}:{spec.python}')
    return (
        f'{head}python {_ROOT}/bin/{COLLECT_MODULE}.py --cache {_ROOT}/collect --key {seed}{select} -- {spec.collect}'
    ).rstrip() + ' || true'


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


def _facts(text: str) -> dict[str, str]:
    _, _, tail = text.partition('@@@ facts')
    return dict(line.split('=', 1) for line in tail.splitlines() if '=' in line)


def _pack(repo: Path, sha: str) -> str:
    """A pack of *sha*'s objects minus what the repo's remote-tracking refs already hold, base64 for a text stdin.

    READ-ONLY, and that is the point: ``git bundle`` takes NAMED refs only, so the bundle this replaced
    wrote a temporary ``refs/lab-ci/<sha>`` into the caller's checkout. ``pack-objects --revs --stdout``
    takes the commit and the exclusions on stdin and writes nothing; the CLUSTER indexes the pack into its
    cache and names the ref there (:func:`fetch_script`).
    """
    git = [_GIT, '-C', str(repo)]
    remotes = subprocess.run(
        [*git, 'for-each-ref', '--format=%(objectname)', 'refs/remotes'], capture_output=True, text=True, check=True
    ).stdout.split()
    revs = '\n'.join([sha, *(f'^{oid}' for oid in remotes)]) + '\n'
    done = subprocess.run(
        [*git, 'pack-objects', '--revs', '--stdout', '-q'], input=revs.encode('ascii'), capture_output=True, check=True
    )
    return base64.b64encode(done.stdout).decode('ascii')


def _shares_home(grant: Grant, builder: Grant) -> bool:
    """Whether *grant* may run a round of the tree *builder* built: same cluster AND same login user.

    The tree, its venv and the item runner live in the BUILDER'S ``~/ci``. Measured 2026-10-09: two
    grants on one cluster under different users (ezxmb14, ezzls2) -- the build ran in one home and the
    rounds went to the other, whose ``~/ci/trees/<sha>`` held no ``.venv``, so 276 shards died on
    ``.venv/bin/activate``. Same cluster is not same home.
    """
    return grant.same_cluster(builder) and grant.user == builder.user


def _state_dir(sha: str) -> str:
    return f'{_ROOT}/runs/{sha}'


def _save(run: Runner, state: dict[str, Any]) -> None:
    run(f'mkdir -p {_state_dir(state["sha"])} && cat > {_state_dir(state["sha"])}/state.json', json.dumps(state))


def _submit_round(
    state: dict[str, Any],
    pending: list[list[str]],
    *,
    runners: dict[str, Runner],
    snapshots: list[tuple[Grant, Snapshot]],
    machine: Machine,
    cost: Cost,
    policy: Policy,
) -> None:
    """Plan and submit round ``len(state['rounds'])`` of *pending* among the grants of *snapshots*."""
    tag = f'{state["stamp"]}-r{len(state["rounds"])}'
    measured = Measured.of(state['measured'])
    seconds, unmeasured = measured.estimate(pending, cost)
    mem_gb = measured.mem_gb(pending, cost, policy) * state['oom']
    grant, plan = allocate(
        len(pending),
        replace(cost, mem_gb=mem_gb),
        snapshots,
        workstation=machine.workstation,
        policy=policy,
        seconds=seconds,
    )
    items = [{'ids': group, 'stream': f'.lab-ci/{tag}/{i}.jsonl'} for i, group in enumerate(pending)]
    setup = (_ENV,)
    sha = state['sha']
    job = JobSpec(name=f'verdict-{sha[:12]}', workdir=f'~/ci/trees/{sha}', setup=setup, entry=f'{ITEM_MODULE}:run')
    sub = submit(runners[grant.account], plan, Config(job=job, cost=cost, policy=policy), items, stamp=tag)
    state['rounds'].append({**_plan_record(plan, grant, sub.job_id, sub.run_dir), 'unmeasured': unmeasured})
    state['current'] = {
        'account': grant.account,
        'tag': tag,
        'items': items,
        'job_id': sub.job_id,
        'shards': sub.shards,
    }


def submit_verdict(
    spec: VerdictSpec,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
    stamp: str | None = None,
    history: dict[str, Any] | None = None,
) -> str:
    """Fetch, build, collect, plan and submit round 0; leave the run's state ON THE CLUSTER. Returns the run id.

    The state is ``~/ci/runs/<sha>/state.json``; :func:`gather_verdict` reads it. *history* is a previous
    record, read for its measurements only. Nothing here waits for a job.
    """
    runners = {g.account: connect(g) for g in machine.grants}
    snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants]
    grant, _ = allocate(1, cost, snapshots, workstation=machine.workstation, policy=policy)
    run = runners[grant.account]
    host = grant.hosts[0]
    said = run(needs_script(spec), None).splitlines()
    lacking = [line.removeprefix('@@@ missing ') for line in said if line.startswith('@@@ missing ')]
    if lacking:
        msg = f'{grant.name} ({host}) lacks {", ".join(lacking)} on its login node; nothing was fetched or built'
        raise RuntimeError(msg)

    if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
        if spec.pack_from is None:
            msg = f'commit {spec.sha} is not on {spec.repo_url}; pass a local repository to pack it from'
            raise RuntimeError(msg)
        run(f'base64 -d > {_ROOT}/packs/{spec.sha}.pack', _pack(spec.pack_from, spec.sha))
        if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
            msg = f'commit {spec.sha} is still missing on {host} after the pack was indexed'
            raise RuntimeError(msg)

    here = Path(__file__).parent
    run(f'cat > {_ROOT}/bin/{COLLECT_MODULE}.py', (here / 'collect.py').read_text(encoding='utf-8'))
    built = run(build_script(spec), (here / 'pytest_item.py').read_text(encoding='utf-8'))
    if BUILD_FAILED in built:
        tail = built.split(BUILD_FAILED, 1)[1].strip()
        msg = f'the build for {spec.sha[:12]} failed on {host}, nothing was submitted; {tail}'
        raise RuntimeError(msg)
    facts = _facts(built)
    collected = run(collect_command(spec), None)
    ids, errored = parse_ids(collected), parse_collection_errors(collected)
    if spec.only is not None:
        ids = [node for node in ids if node in spec.only]
        errored = [node for node in errored if any(n.split('::')[0] == node for n in spec.only)]
    if not ids:
        msg = f'no test was collected in {spec.tree} with {spec.collect!r} -- an empty run is not a pass'
        raise RuntimeError(msg)
    cannot = {
        platform: set(parse_ids(run(collect_command(spec, also=expr), None)))
        for platform in PLATFORMS
        if (expr := cannot_run(spec.table, platform))
    }
    handed = {node: can for node, can in sorted(runnable(ids, cannot).items()) if assign(can) != LINUX}
    outcomes: dict[str, str] = dict.fromkeys(handed, 'not-covered')
    outcomes.update(dict.fromkeys(errored, 'error'))
    state: dict[str, Any] = {
        'sha': spec.sha,
        'stamp': stamp or time.strftime('%Y%m%d-%H%M%S'),
        'platform': facts.get('platform', ''),
        'cluster': host,
        'python': facts.get('python', ''),
        'handed': handed,
        'outcomes': outcomes,
        'measured': Measured.of(history or {}).record(),
        'rounds': [],
        'oom': 1,
    }
    pending = group_items([node for node in ids if node not in handed])
    same_cluster = [(g, s) for g, s in snapshots if _shares_home(g, grant)]
    _submit_round(state, pending, runners=runners, snapshots=same_cluster, machine=machine, cost=cost, policy=policy)
    _save(runners[state['current']['account']], state)
    return spec.sha


def _load(sha: str, machine: Machine, runners: dict[str, Runner]) -> tuple[dict[str, Any], Runner]:
    """The state of *sha*'s run from whichever grant holds it; :class:`Unreachable` when no grant answered."""
    answered = False
    for grant in machine.grants:
        try:
            text = runners[grant.account](f'cat {_state_dir(sha)}/state.json 2>/dev/null || true', None)
        except Unreachable:
            continue
        answered = True
        if text.strip():
            return json.loads(text), runners[grant.account]
    if not answered:
        msg = f'no login host of any grant answered; the jobs of {sha[:12]} keep running -- gather again later'
        raise Unreachable(msg)
    msg = f'no verdict of {sha} was submitted on any grant (no {_state_dir(sha)}/state.json)'
    raise RuntimeError(msg)


def gather_verdict(
    sha: str,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
) -> dict[str, Any] | None:
    """ONE short call: the record when the run is done, else ``None`` (pending).

    A finished round is folded; what a killed shard left unfinished is re-submitted (up to :data:`RETRIES`
    more rounds) and the call returns pending -- THE RETRIES ARE DRIVEN BY GATHER CALLS, nothing resident
    waits. An outage raises :class:`Unreachable` and loses nothing: the jobs keep running and the state
    stays on the cluster, so the next gather picks up where this one could not.
    """
    runners = {g.account: connect(g) for g in machine.grants}
    state, home = _load(sha, machine, runners)
    if 'record' in state:
        return state['record']
    current = state['current']
    account = tuple(current['account'])
    run = runners[account]
    states = shard_states(run, [current['job_id']])
    if len(states) < current['shards'] or any(s in ACTIVE for s in states.values()):
        return None
    measured = Measured.of(state['measured'])
    streams = read_streams(run, f'{_ROOT}/trees/{sha}/.lab-ci/{current["tag"]}')
    pending = _fold(current['items'], streams, state['outcomes'], measured)
    state['measured'] = measured.record()
    if 'OUT_OF_MEMORY' in states.values():
        state['oom'] *= 2
    if pending and len(state['rounds']) <= RETRIES:
        grant = next(g for g in machine.grants if g.account == account)
        snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants if _shares_home(g, grant)]
        _submit_round(state, pending, runners=runners, snapshots=snapshots, machine=machine, cost=cost, policy=policy)
        _save(home, state)
        return None
    state['outcomes'].update({node: 'lost' for group in pending for node in group})
    record = {
        **{key: state[key] for key in ('sha', 'platform', 'cluster', 'python', 'rounds', 'outcomes', 'handed')},
        'plan': state['rounds'][0],
        **state['measured'],
    }
    state['record'] = record
    _save(home, state)
    return record


def _fold(
    items: list[dict[str, Any]], streams: dict[int, str], outcomes: dict[str, str], measured: Measured
) -> list[list[str]]:
    """Record one round's streams into *outcomes* and *measured*; return the unfinished ids, halved."""
    pending: list[list[str]] = []
    for index, item in enumerate(items):
        group = item['ids']
        folded = read_stream(streams.get(index, ''), group)
        outcomes.update(folded['outcomes'])
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['outcomes']]
        if folded['done'] is not None:
            outcomes.update(dict.fromkeys(rest, 'missing'))
            continue
        half = (len(rest) + 1) // 2
        pending += [part for part in (rest[:half], rest[half:]) if part]
    return pending


def read_streams(run: Runner, directory: str) -> dict[int, str]:
    """Every item stream under *directory* (``<index>.jsonl``), keyed by item index -- one remote command."""
    listing = 'for f in *.jsonl; do [ -e "$f" ] && echo "@@@ $f" && cat "$f"; done'
    out = run(f'cd {directory} 2>/dev/null && {listing}; true', None)
    streams: dict[int, list[str]] = {}
    current: list[str] = []
    for line in out.splitlines():
        if line.startswith('@@@ ') and line.endswith('.jsonl'):
            current = streams.setdefault(int(line[4:-6]), [])
        else:
            current.append(line)
    return {index: '\n'.join(lines) for index, lines in streams.items()}


def _plan_record(plan: Plan, grant: Grant, job_id: str, run_dir: str) -> dict[str, Any]:
    return {**asdict(plan), 'grant': grant.name, 'job_id': job_id, 'run_dir': run_dir}


def summary(record: dict[str, Any]) -> str:
    """``passed=…, failed=…`` counts of a record, worst first."""
    counts: dict[str, int] = {}
    for outcome in record['outcomes'].values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return ', '.join(f'{k}={v}' for k, v in sorted(counts.items()))


def write_record(record: dict[str, Any], target: Path) -> None:
    """The record as JSON at *target* -- the caller's chosen path, created with its parent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, sort_keys=True), encoding='utf-8')
