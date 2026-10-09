"""A remote verdict: one commit's test suite, sharded over a cluster, gathered into a record bound to the commit.

EVERYTHING LIVES UNDER ``~/ci/`` ON THE CLUSTER, AND NOTHING ELSE IS TOUCHED. A day-to-day checkout in
``~/<repo>`` and its venv are someone's working state; a verdict must neither reinstall nor read them::

    ~/ci/cache.git              bare cache; fetched from the read-only HTTPS remote
    ~/ci/bundles/<sha>.bundle   only when the commit is not on the remote (sent over stdin)
    ~/ci/trees/<sha>/           a worktree of the cache, with its OWN .venv (reused when present)
    ~/ci/bin/lab_ci_pytest_item.py   the item runner, shipped from this checkout (pytest_item.py)

THE FLOW. Probe every grant and pick the cluster (:func:`lab_commons.hpc.plan.allocate`); fetch the
commit (or ship a bundle); add the tree; build its venv on the login node with the caller's install
command; collect node ids there; group them by file into items; re-allocate the real item count among
the grants on that cluster; submit the array; poll ``sacct`` until no shard is active; gather.

NOT COVERED IS NOT PASSED. The commit's own ``[tool.lab_commons.platforms]`` table
(:mod:`lab_commons.hpc.platforms`) says which markers linux cannot run; those ids are collected
separately, recorded as ``not-covered``, and listed in the record's ``left`` with the platforms that CAN
run each (``[]`` when none can) -- the input the next platform part selects from. An id whose shard died
is ``lost``; an id the junit file never mentions is ``missing``. Only ``passed`` means passed.
"""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import Plan, allocate
from lab_commons.hpc.platforms import PLATFORMS, cannot_run
from lab_commons.hpc.run import Runner, Unreachable, gather, probe, shard_states, submit

__all__ = [
    'ACTIVE',
    'ITEM_MODULE',
    'VerdictSpec',
    'build_script',
    'collect_command',
    'fetch_script',
    'group_items',
    'parse_collection_errors',
    'parse_ids',
    'remote_verdict',
    'summary',
    'write_record',
]

#: Slurm states during which a shard may still write its results.
ACTIVE: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'CONFIGURING', 'COMPLETING', 'RESIZING', 'SUSPENDED'})

#: The item runner's name on the cluster -- unique, so it can shadow nothing in the tested project.
ITEM_MODULE: Final = 'lab_ci_pytest_item'

_ROOT: Final = '"$HOME"/ci'

#: Hex digits of a full SHA-1 commit id.
_SHA_HEX: Final = 40

#: The tree's venv first on PATH, so its ``python`` runs -- the cluster is POSIX whatever box submits.
_VENV: Final = 'PATH="$PWD/.venv/bin:$PATH"'


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
    bundle_from: Path | None = None

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
    """Make the cache hold the commit -- remote heads first, then a shipped bundle. Prints ``have``/``missing``."""
    sha, url, cache = spec.sha, shlex.quote(spec.repo_url), f'{_ROOT}/cache.git'
    return '\n'.join(
        [
            'set -eu',
            f'mkdir -p {_ROOT}/trees {_ROOT}/bundles {_ROOT}/bin',
            f'[ -d {cache} ] || git init -q --bare {cache}',
            f'have() {{ git -C {cache} cat-file -e {sha}^{{commit}} 2>/dev/null; }}',
            f'have || git -C {cache} fetch -q {url} "+refs/heads/*:refs/remotes/origin/*"',
            f'have || git -C {cache} fetch -q {url} {sha} 2>/dev/null || true',
            f'B={_ROOT}/bundles/{sha}.bundle',
            f'have || {{ [ -s "$B" ] && git -C {cache} fetch -q "$B" "+refs/lab-ci/*:refs/lab-ci/*"; }} || true',
            'if have; then echo have; else echo missing; fi',
        ]
    )


def build_script(spec: VerdictSpec) -> str:
    """Add the tree, build its venv once, install the item runner. Prints ``@@@ facts`` then ``key=value`` lines.

    The item runner's source arrives on stdin, so the script's first line is a ``cat``.
    """
    cache, tree = f'{_ROOT}/cache.git', f'{_ROOT}/trees/{spec.sha}'
    python = f' --python {shlex.quote(spec.python)}' if spec.python else ''
    return '\n'.join(
        [
            'set -eo pipefail',
            f'cat > {_ROOT}/bin/{ITEM_MODULE}.py',
            f'[ -d {tree} ] || git -C {cache} worktree add -q --detach {tree} {spec.sha}',
            f'cd {tree}',
            'if [ ! -f .venv/.lab-ci-installed ]; then',
            f'  uv venv -q --allow-existing{python} .venv',
            '  export VIRTUAL_ENV="$PWD/.venv" PATH="$PWD/.venv/bin:$PATH"',
            f'  {spec.install}',
            '  touch .venv/.lab-ci-installed',
            'fi',
            'echo "@@@ facts"',
            'echo "platform=$(uname -s | tr A-Z a-z)-$(uname -m)/glibc$(getconf GNU_LIBC_VERSION | cut -d" " -f2)"',
            f'echo "python=$({_VENV} python -c "import platform; print(platform.python_version())")"',
        ]
    )


def collect_command(spec: VerdictSpec, *, also: str = '') -> str:
    """``pytest --collect-only`` in the tree; ONE ``-m`` joins ``select`` and the marker expression *also*.

    One expression, because pytest keeps only the last ``-m`` -- a second one would silently drop the first.
    """
    parts = [f'({spec.select})'] if spec.select else []
    if also:
        parts.append(f'({also})')
    select = f' -m {shlex.quote(" and ".join(parts))}' if parts else ''
    return (
        f'cd {_ROOT}/trees/{spec.sha} && {_VENV} python -m pytest --collect-only -q -p no:cacheprovider'
        f' --continue-on-collection-errors{select} {spec.collect}'
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


def group_items(ids: Sequence[str]) -> list[dict[str, Any]]:
    """One item per test file, in first-seen order; each writes its own junit file under the tree."""
    files: dict[str, list[str]] = {}
    for node in ids:
        files.setdefault(node.split('::')[0], []).append(node)
    return [{'ids': group, 'junit': f'.lab-ci/junit/{i}.xml'} for i, group in enumerate(files.values())]


def _facts(text: str) -> dict[str, str]:
    _, _, tail = text.partition('@@@ facts')
    return dict(line.split('=', 1) for line in tail.splitlines() if '=' in line)


def _bundle(repo: Path, sha: str) -> str:
    """A bundle of *sha* minus what the repo's remotes already hold, base64 for a text stdin.

    ``git bundle`` takes NAMED refs only, so the commit gets a temporary ``refs/lab-ci/<sha>`` for the
    length of the call and loses it after -- the one write this makes to the caller's repository.
    """
    ref = f'refs/lab-ci/{sha}'
    git = ['git', '-C', str(repo)]
    subprocess.run([*git, 'update-ref', ref, sha], check=True)
    try:
        done = subprocess.run(
            [*git, 'bundle', 'create', '-', ref, '--not', '--remotes'], capture_output=True, check=True
        )
    finally:
        subprocess.run([*git, 'update-ref', '-d', ref], check=True)
    return base64.b64encode(done.stdout).decode('ascii')


#: How long the cluster may stay unreachable while a verdict waits. The jobs keep running through an outage
#: (a VPN drop, a login node reboot); only the watcher is cut off, so a drop is waited out, not fatal.
OUTAGE_CEILING_S: Final = 6 * 3600.0


def _wait(run: Runner, job_id: str, shards: int, poll: float, sleep: Callable[[float], None]) -> dict[int, str]:
    unreachable_s = 0.0
    while True:
        try:
            states = shard_states(run, [job_id])
        except Unreachable as exc:
            unreachable_s += poll
            if unreachable_s > OUTAGE_CEILING_S:
                msg = (
                    f'job {job_id} is still on the cluster but no login host answered for {unreachable_s:.0f} s: {exc}'
                )
                raise Unreachable(msg) from exc
        else:
            unreachable_s = 0.0
            if len(states) >= shards and not any(state in ACTIVE for state in states.values()):
                return states
        sleep(poll)


def remote_verdict(
    spec: VerdictSpec,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
    poll: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    stamp: str | None = None,
) -> dict[str, Any]:
    """The module docstring's flow; the record is ``{sha, platform, cluster, python, plan, outcomes, left}``."""
    runners = {g.account: connect(g) for g in machine.grants}
    snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants]
    grant, _ = allocate(1, cost, snapshots, workstation=machine.workstation, policy=policy)
    run = runners[grant.account]
    host = grant.hosts[0]

    if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
        if spec.bundle_from is None:
            msg = f'commit {spec.sha} is not on {spec.repo_url}; pass a local repository to bundle it from'
            raise RuntimeError(msg)
        run(f'base64 -d > {_ROOT}/bundles/{spec.sha}.bundle', _bundle(spec.bundle_from, spec.sha))
        if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
            msg = f'commit {spec.sha} is still missing on {host} after the bundle was fetched'
            raise RuntimeError(msg)

    item_source = (Path(__file__).with_name('pytest_item.py')).read_text(encoding='utf-8')
    facts = _facts(run(build_script(spec), item_source))
    collected = run(collect_command(spec), None)
    ids, errored = parse_ids(collected), parse_collection_errors(collected)
    if not ids:
        msg = f'no test was collected in {spec.tree} with {spec.collect!r} -- an empty run is not a pass'
        raise RuntimeError(msg)
    cannot = {
        platform: set(parse_ids(run(collect_command(spec, also=expr), None)))
        for platform in PLATFORMS
        if (expr := cannot_run(spec.table, platform))
    }
    not_covered = {node for node in ids if node in cannot.get('linux', set())}
    left = {node: [p for p in PLATFORMS if node not in cannot.get(p, set())] for node in sorted(not_covered)}
    items = group_items([node for node in ids if node not in not_covered])

    same_cluster = [(g, s) for g, s in snapshots if g.same_cluster(grant)]
    grant, plan = allocate(len(items), cost, same_cluster, workstation=machine.workstation, policy=policy)
    run = runners[grant.account]
    setup = ('source .venv/bin/activate', 'export PYTHONPATH="$HOME/ci/bin${PYTHONPATH:+:$PYTHONPATH}"')
    job = JobSpec(name=f'verdict-{spec.sha[:12]}', workdir=spec.tree, setup=setup, entry=f'{ITEM_MODULE}:run')
    config = Config(job=job, cost=cost, policy=policy)
    sub = submit(run, plan, config, items, stamp=stamp or time.strftime('%Y%m%d-%H%M%S'))
    _wait(run, sub.job_id, sub.shards, poll, sleep)
    returned = {r['index']: r for shard in gather(run, sub.run_dir).values() for r in shard}

    outcomes: dict[str, str] = dict.fromkeys(sorted(not_covered), 'not-covered')
    outcomes.update(dict.fromkeys(errored, 'error'))
    for index, item in enumerate(items):
        result = returned.get(index)
        got = result['value']['outcomes'] if result and result['ok'] else {}
        outcomes.update({node: got.get(node, 'lost') for node in item['ids']})
    return {
        'sha': spec.sha,
        'platform': facts.get('platform', ''),
        'cluster': host,
        'python': facts.get('python', ''),
        'plan': _plan_record(plan, grant, sub.job_id, sub.run_dir),
        'outcomes': outcomes,
        'left': left,
    }


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
