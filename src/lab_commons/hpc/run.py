"""A remote run: one commit's test suite, sharded over a cluster, gathered into a record bound to the commit.

EVERYTHING LIVES UNDER ``~/ci/`` ON THE CLUSTER, AND NOTHING ELSE IS TOUCHED. A day-to-day checkout in
``~/<repo>`` and its venv are someone's working state; a run must neither reinstall nor read them::

    ~/ci/cache.git              bare cache; fetched from the read-only HTTPS remote
    ~/ci/packs/<sha>.pack       only when the commit is not on the remote (sent over stdin, indexed here)
    ~/ci/runs/<sha>/state.json  the run: its rounds, outcomes so far and the job of the round in flight
    ~/ci/trees/<sha>/           a worktree of the cache, with its OWN .venv (reused when present)
    ~/ci/bin/lab_ci_pytest_item.py   the item runner, shipped from this checkout (pytest_item.py)

THE FLOW. Probe every grant and pick the cluster (:func:`lab_commons.hpc.plan.allocate`); fetch the
commit (or ship a pack); add the tree; build its venv on the login node with the caller's install
command; collect node ids there; group them by file and PACK whole files into items of up to
:func:`item_seconds` measured seconds, so start-up is paid per item, not per file; re-allocate the real item count among
the grants on that cluster; submit the array and leave the run's state on the cluster
(:func:`submit_run`, minutes). Then each :func:`gather_run` is ONE short call: still active is
pending; a finished round is folded, its unfinished ids re-submitted as the next round, or the record
returned. NOTHING RESIDENT RUNS ON THE CALLER'S BOX, and the caller's repository is only READ.

NOT COVERED IS NOT PASSED. The commit's own ``[tool.lab_commons.platforms]`` table
(:mod:`lab_commons.hpc.platforms`) says which markers each platform cannot run; an id the
assignment rule (:func:`lab_commons.hpc.platforms.assign`) gives to another part is recorded as
``not-covered`` and listed in the record's ``handed`` with the platforms that CAN run it (``[]`` when
none can). Admission composes it with the other parts; no part reads it to choose its ids. Only
``passed`` means passed.

A KILLED SHARD COSTS ONLY WHAT IT DID NOT FINISH. Every item streams its outcomes as they happen
(:mod:`lab_commons.hpc.pytest_item`); the run reads the streams, not the shards' end-of-run files.
An item whose stream never closed is unfinished: its unreported ids are re-submitted, split in halves,
for up to :data:`RETRIES` more rounds -- a round after an ``OUT_OF_MEMORY`` asks for twice the memory --
and only what is still unfinished after that is ``lost``. An item that closed without reporting an id
recorded it ``missing``.

THE PLAN IS MEASURED. The record keeps ``durations`` (seconds per id), ``overheads`` (per file: wall time
minus its tests', i.e. interpreter and imports) and ``peaks_mb`` (per file); the next run reads them
back (``history``), so an item's time is its file's overhead plus its ids' durations, the array's
memory is the worst measured peak times ``policy.safety``, and ``cost`` is the fallback only for a file
never measured -- named in the plan as ``unmeasured``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.cluster import Runner, Unreachable, probe, shard_states, submit
from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.measured import Measured
from lab_commons.hpc.plan import Plan, allocate
from lab_commons.hpc.platforms import LINUX, PLATFORMS, assign, cannot_run, runnable
from lab_commons.hpc.records import fold, read_streams
from lab_commons.hpc.shell import (
    BUILD_FAILED,
    COLLECT_MODULE,
    ENV,
    ITEM_MODULE,
    ROOT,
    RunSpec,
    build_script,
    collect_command,
    facts,
    fetch_script,
    group_items,
    needs_script,
    pack,
    parse_collection_errors,
    parse_ids,
    parse_times,
)
from lab_commons.hpc.slurm import Snapshot

__all__ = ['ACTIVE', 'RETRIES', 'gather_run', 'item_seconds', 'submit_run']


def item_seconds(policy: Policy) -> float:
    """An item's budget: the shortest shard, unpadded -- so a padded item never alone passes ``shard_minutes_min``."""
    return policy.shard_minutes_min * 60 / policy.safety


#: Slurm states during which a shard may still write its results.
ACTIVE: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'CONFIGURING', 'COMPLETING', 'RESIZING', 'SUSPENDED'})

#: Rounds after the first that re-run what a killed shard left unfinished.
RETRIES: Final = 2

#: State keys the record carries as they are.
_CARRIED: Final = (
    'sha',
    'scope',
    'platform',
    'system',
    'env',
    'cluster',
    'python',
    'prep',
    'rounds',
    'outcomes',
    'handed',
)


def _shares_home(grant: Grant, builder: Grant) -> bool:
    """Whether *grant* may run a round of the tree *builder* built: same cluster AND same login user.

    The tree, its venv and the item runner live in the BUILDER'S ``~/ci``. Measured 2026-10-09: two
    grants on one cluster under different users -- the build ran in one home and the
    rounds went to the other, whose ``~/ci/trees/<sha>`` held no ``.venv``, so 276 shards died on
    ``.venv/bin/activate``. Same cluster is not same home.
    """
    return grant.same_cluster(builder) and grant.user == builder.user


def _state_dir(sha: str) -> str:
    return f'{ROOT}/runs/{sha}'


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
    setup = (ENV,)
    sha = state['sha']
    job = JobSpec(name=f'run-{sha[:12]}', workdir=f'~/ci/trees/{sha}', setup=setup, entry=f'{ITEM_MODULE}:run')
    sub = submit(runners[grant.account], plan, Config(job=job, cost=cost, policy=policy), items, stamp=tag)
    state['rounds'].append({**_plan_record(plan, grant, sub.job_id, sub.run_dir), 'unmeasured': unmeasured})
    state['current'] = {
        'account': grant.account,
        'tag': tag,
        'items': items,
        'job_id': sub.job_id,
        'shards': sub.shards,
    }


def submit_run(
    spec: RunSpec,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
    stamp: str | None = None,
    history: dict[str, Any] | None = None,
) -> str:
    """Fetch, build, collect, plan and submit round 0; leave the run's state ON THE CLUSTER. Returns the run id.

    The state is ``~/ci/runs/<sha>/state.json``; :func:`gather_run` reads it. *history* is a previous
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
        run(f'base64 -d > {ROOT}/packs/{spec.sha}.pack', pack(spec.pack_from, spec.sha))
        if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
            msg = f'commit {spec.sha} is still missing on {host} after the pack was indexed'
            raise RuntimeError(msg)

    here = Path(__file__).parent
    run(f'cat > {ROOT}/bin/{COLLECT_MODULE}.py', (here / 'collect.py').read_text(encoding='utf-8'))
    built = run(build_script(spec), (here / 'pytest_item.py').read_text(encoding='utf-8'))
    if BUILD_FAILED in built:
        tail = built.split(BUILD_FAILED, 1)[1].strip()
        msg = f'the build for {spec.sha[:12]} failed on {host}, nothing was submitted; {tail}'
        raise RuntimeError(msg)
    told = facts(built)
    if not told.get('env'):
        msg = (
            f'the venv built for {spec.sha[:12]} on {host} reported no env hash (lab_commons.dev.envkey is not '
            'importable there), so its part could never be carried; nothing was submitted'
        )
        raise RuntimeError(msg)
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
        'scope': spec.scope,
        'platform': LINUX,
        'system': told.get('platform', ''),
        'env': told['env'],
        'cluster': host,
        'python': told.get('python', ''),
        'prep': {**parse_times(built), **parse_times(collected)},
        'handed': handed,
        'outcomes': outcomes,
        'measured': Measured.of(history or {}).record(),
        'rounds': [],
        'oom': 1,
    }
    files = group_items([node for node in ids if node not in handed])
    pending = Measured.of(state['measured']).pack(files, cost, item_seconds(policy))
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
    msg = f'no run of {sha} was submitted on any grant (no {_state_dir(sha)}/state.json)'
    raise RuntimeError(msg)


def gather_run(
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
    streams = read_streams(run, f'{ROOT}/trees/{sha}/.lab-ci/{current["tag"]}')
    pending, killed = fold(current['items'], streams, state['outcomes'], measured, state.setdefault('reasons', {}))
    state['measured'] = measured.record()
    if killed or 'OUT_OF_MEMORY' in states.values():
        state['oom'] *= 2
    if pending and len(state['rounds']) <= RETRIES:
        grant = next(g for g in machine.grants if g.account == account)
        snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants if _shares_home(g, grant)]
        _submit_round(state, pending, runners=runners, snapshots=snapshots, machine=machine, cost=cost, policy=policy)
        _save(home, state)
        return None
    state['outcomes'].update({node: 'lost' for group in pending for node in group})
    record = {
        **{key: state[key] for key in _CARRIED},
        'req': state['stamp'],
        'account': next(g.slurm_account for g in machine.grants if g.account == account),
        'job_ids': [r['job_id'] for r in state['rounds']],
        'plan': state['rounds'][0],
        'reasons': {n: why for n, why in state['reasons'].items() if state['outcomes'].get(n) in ('failed', 'error')},
        **state['measured'],
    }
    state['record'] = record
    _save(home, state)
    return record


def _plan_record(plan: Plan, grant: Grant, job_id: str, run_dir: str) -> dict[str, Any]:
    return {**asdict(plan), 'grant': grant.name, 'job_id': job_id, 'run_dir': run_dir}
