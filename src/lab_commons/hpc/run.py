"""A remote run: one commit's test suite, sharded over a cluster, gathered into a record bound to the commit.

EVERYTHING LIVES UNDER ``~/ci/`` ON THE CLUSTER, AND NOTHING ELSE IS TOUCHED. A day-to-day checkout in
``~/<repo>`` and its venv are someone's working state; a run must neither reinstall nor read them::

    ~/ci/cache/<repo>.git       one bare cache PER REPOSITORY (shell.cache_dir); origin = its URL, heads pruned
    ~/ci/packs/<sha>.pack       only when the commit is not on the remote (sent over stdin, indexed here)
    ~/ci/runs/<sha>/<part>/state.json  the run of one part: its rounds, cells so far and the job in flight
    ~/ci/trees/<sha>/           a worktree of the cache, with its OWN .venv (reused when present)
    ~/ci/bin/lab_ci_pytest_item.py   the item runner, shipped from this checkout (pytest_item.py)

THE FLOW. Probe every grant and pick the cluster (:func:`lab_commons.hpc.plan.allocate`); fetch the
commit (or ship a pack); add the tree; build its venv on the login node with the caller's install
command; collect node ids there; group them by file and PACK whole files into items of up to
:func:`item_seconds` measured seconds, so start-up is paid per item, not per file; re-allocate the real item count among
the grants on that cluster; submit the array and leave the run's state on the cluster
(:func:`submit_run`, minutes). Then each :func:`lab_commons.hpc.gather.gather_run` is ONE short call: still active is
pending; a finished round is folded, its unfinished ids re-submitted as the next round, or the record
returned. NOTHING RESIDENT RUNS ON THE CALLER'S BOX, and the caller's repository is only READ.

NOT COVERED IS NOT PASSED. A run produces ONE part, its spec's need-set (:mod:`lab_commons.hpc.needsets`):
every collected id belonging to another part -- by the resource markers it carries against the commit's
``[tool.lab_commons.tests] resources`` -- is no cell of this run: it is listed only in the record's
``handed`` with the part it belongs to. Admission composes it with the other parts; no part reads it to
choose its ids. Only a DECIDED cell is an answer (:mod:`lab_commons.hpc.testcells`).

A KILLED SHARD COSTS ONLY WHAT IT DID NOT FINISH. Every item streams its test cells as they happen
(:mod:`lab_commons.hpc.pytest_item`); the run reads the streams, not the shards' end-of-run files.
An item whose stream never closed is unfinished: its unreported ids are re-submitted, split in halves,
for up to :data:`RETRIES` more rounds -- a round after a ``TIMEOUT`` asks for at least twice the minutes
(held to the partition's ceiling, preferring one that holds them all), and the id a kill interrupted is
priced at no less than the time it was given (:meth:`lab_commons.compute.learned.Measured.killed`) -- and
only what is still unfinished after that is ``INFRA``. An item that closed without reporting an id
recorded it ``PENDING``: never reported.

AN ``OUT_OF_MEMORY`` BISECTS, IT NEVER OUTGROWS EVERY GRANT. The round after one asks twice the memory for
the files of the KILLED items only (:func:`lab_commons.hpc.gather.grown_after`; a file that then finishes is back at its
peak), HELD TO the most any grant gives (:func:`lab_commons.hpc.plan.grantable_gb`), and OOM rounds are not
counted against :data:`RETRIES` while that ceiling exists: each one either grows the ask toward it or
halves the killed items, so they end. A lone test still killed at the ceiling is ``failed`` by id
(:func:`lab_commons.hpc.records.fold`). A round that NO GRANT CAN TAKE settles at once: its ids are
``error`` with the refusal as their reason, and the record is written -- never a run that waits forever.

THE PLAN IS MEASURED. The record keeps ``durations`` (seconds per id), ``overheads`` (per file: wall time
minus its tests', i.e. interpreter and imports) and ``peaks_mb`` (per file); the next run reads them
back (``history``), so an item's time is its file's overhead plus its ids' durations, an item's memory
is its own files' worst measured peak times ``policy.safety`` -- items grouped by memory class, one array
per class (:func:`submit_round`) -- and ``cost`` is the fallback only for a file never measured -- named
in the plan as ``unmeasured``. EVERY FOLDED ROUND is learned (:mod:`lab_commons.compute.learned`), not only
the record: measured 2026-10-13 on Ada, nine downstream runs were each superseded before their record, so
none was learned and every round 0 planned blind.
"""

from __future__ import annotations

import json
import math
import shlex
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from typing import Any, Final

from lab_commons.compute import learned
from lab_commons.compute.learned import Measured
from lab_commons.hpc.cluster import Runner, SubmitLimit, Unreachable, probe, submit_bands
from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.needsets import need_sets, needs_of
from lab_commons.hpc.plan import SUBMIT_ROOM_SPENT, Plan, allocate, grantable_gb, spend
from lab_commons.hpc.records import (
    record_of,
    upgraded_state,
)
from lab_commons.hpc.shards import balance
from lab_commons.hpc.shell import (
    ENV,
    ITEM_MODULE,
    ROOT,
    RunSpec,
    collect_command,
    group_items,
    nothing_collected,
    parse_collection_errors,
    parse_ids,
    parse_times,
)
from lab_commons.hpc.slurm import Snapshot
from lab_commons.hpc.testcells import TestCell
from lab_commons.hpc.tree import NotSubmitted, build_run
from lab_commons.outcomes import Cell, Reason, Status

__all__ = [
    'ACTIVE',
    'RETRIES',
    'NoRoom',
    'cancel_run',
    'item_seconds',
    'load_state',
    'save_state',
    'shares_home',
    'submit_round',
    'submit_run',
    'withdraw_pending',
]


class NoRoom(NotSubmitted):
    """The HOST had no room for the part (no headroom at first submit, or held on an account/QOS limit).

    A fact about the host, never a result about the sha: it is released RETRYABLE, so the part
    stays open to every host, and the learned ask keeps this one from re-admitting it at the same size.
    """


def item_seconds(policy: Policy) -> float:
    """An item's budget: the shortest shard, unpadded -- so a padded item never alone passes ``shard_minutes_min``."""
    return policy.shard_minutes_min * 60 / policy.safety


#: Slurm states during which a shard may still write its results.
ACTIVE: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'CONFIGURING', 'COMPLETING', 'RESIZING', 'SUSPENDED'})

#: Rounds after the first that re-run what a killed shard left unfinished.
RETRIES: Final = 2


def shares_home(grant: Grant, builder: Grant) -> bool:
    """Whether *grant* may run a round of the tree *builder* built: same cluster AND same login user.

    The tree, its venv and the item runner live in the BUILDER'S ``~/ci``. Measured 2026-10-09: two
    grants on one cluster under different users -- the build ran in one home and the
    rounds went to the other, whose ``~/ci/trees/<sha>`` held no ``.venv``, so 276 shards died on
    ``.venv/bin/activate``. Same cluster is not same home.
    """
    return grant.same_cluster(builder) and grant.user == builder.user


def _state_dir(sha: str, part: str) -> str:
    """One run is one PART of one sha: two parts of a sha are two runs, never one state (Ada, 2026-10-17)."""
    return f'{ROOT}/runs/{sha}/{part}'


def save_state(run: Runner, state: dict[str, Any]) -> None:
    """Write *state* to its run's ``state.json`` on the cluster *run* reaches."""
    where = _state_dir(state['sha'], state['platform'])
    run(f'mkdir -p {where} && cat > {where}/state.json', json.dumps(state))


@contextmanager
def _no_room() -> Iterator[None]:
    """A refusal for want of QOS submits -- planned (:data:`SUBMIT_ROOM_SPENT`) or Slurm's -- as :class:`NoRoom`.

    Ada 2026-10-21: an ``sbatch`` refused on :data:`~lab_commons.hpc.cluster.SUBMIT_LIMIT` stuck its run on
    ``bash exited 1`` every tick, holding the claim while other grants had headroom. The probe's room can be
    stale (another session submits between the probe and the ``sbatch``), so the refusal itself is the signal.
    """
    try:
        yield
    except SubmitLimit as refused:
        msg = f'the QOS refused the round, so a host with room takes it: {refused}'
        raise NoRoom(msg) from refused
    except ValueError as refused:
        if SUBMIT_ROOM_SPENT not in str(refused):
            raise
        msg = f'nothing was submitted: {refused}'
        raise NoRoom(msg) from refused


@_no_room()
def submit_round(
    state: dict[str, Any],
    pending: list[list[str]],
    *,
    runners: dict[str, Runner],
    snapshots: list[tuple[Grant, Snapshot]],
    machine: Machine,
    cost: Cost,
    policy: Policy,
) -> None:
    """Plan and submit round ``len(state['rounds'])`` of *pending* among the grants of *snapshots*.

    EACH ITEM ASKS ITS OWN MEMORY (:meth:`Measured.item_gb`: its files' learned peaks times the safety, times
    this run's growth of a file whose item was OOM-killed), held to the most any grant gives. A Slurm array
    asks one memory for all its tasks, so items are grouped by memory class (:func:`learned.memory_band`) and
    each class is its own array of one manifest (:func:`submit_bands`). The heaviest class picks the grant;
    the others plan on it, since the tree lives in its home.
    """
    tag = f'{state["stamp"]}-r{len(state["rounds"])}'
    measured = Measured.of(state['measured'])
    grown = state.get('grown', {})
    grantable = grantable_gb(snapshots)
    asks = [measured.item_gb(ids, cost, policy, grown) for ids in pending]
    held = [ask if grantable is None else min(ask, grantable) for ask in asks]
    classes: dict[int, list[int]] = {}
    for index, gb in enumerate(held):
        classes.setdefault(learned.memory_band(gb), []).append(index)
    min_minutes = state.get('min_minutes', 0.0)
    chosen: Grant | None = None
    order: list[int] = []
    plans: list[Plan] = []
    bands: list[dict[str, Any]] = []
    unmeasured: set[str] = set()
    for key in sorted(classes, reverse=True):
        members = classes[key]
        group = [pending[i] for i in members]
        seconds, guessed = measured.estimate(group, cost)
        mem_gb = max(held[i] for i in members)
        options = snapshots if chosen is None else [(g, s) for g, s in snapshots if g == chosen]
        chosen, plan = allocate(
            len(group),
            replace(cost, mem_gb=mem_gb),
            options,
            session=machine.session,
            policy=policy,
            seconds=seconds,
            paging=machine.paging,
            rerun_safe=True,  # a forge test part reruns to the same verdict
            min_minutes=min_minutes,
        )
        if not state['rounds'] and plan.room == 0:
            needs = needs_of(state['platform'])
            learned.record_ask(state['repo'], learned.SLURM, needs, mem_gb, safety=policy.safety)
            msg = (
                f'{chosen.name} has no headroom now for one task of {cost.cpus} CPU and {mem_gb:.1f} GB beside what '
                "the account's own jobs hold; nothing was submitted, so a host with room takes it"
            )
            raise NoRoom(msg)
        local, shards = balance(seconds, plan.shards)
        snapshots = spend(snapshots, chosen, plan.qos, len(shards))  # the bands of a round share one submit room
        plan = _rewalled(plan, shards, [seconds[i] for i in local], policy, min_minutes)
        base, first = len(order), sum(len(p.shards) for p in plans)
        order += [members[i] for i in local]
        plans.append(replace(plan, shards=tuple((a + base, b + base) for a, b in plan.shards)))
        at_ceiling = None if grantable is None else max(asks[i] for i in members) >= grantable
        bands.append(
            {
                'shards': [first, first + len(plan.shards)],
                'mem_mb': plan.mem_mb,
                'minutes': plan.minutes,
                'throttle': plan.throttle,
                'at_ceiling': at_ceiling,
            }
        )
        unmeasured |= set(guessed)
    if chosen is None:
        msg = 'a round was planned over no pending items'
        raise ValueError(msg)
    pending = [pending[i] for i in order]
    ceiling = {'timeout_s': state['timeout_s']} if state.get('timeout_s') else {}
    items = [{'ids': group, 'stream': f'.lab-ci/{tag}.jsonl', 'item': i, **ceiling} for i, group in enumerate(pending)]
    sha = state['sha']
    job = JobSpec(
        name=job_name(sha, state['platform']), workdir=f'~/ci/trees/{sha}', setup=(ENV,), entry=f'{ITEM_MODULE}:run'
    )
    config = Config(job=job, cost=cost, policy=policy)
    run_dir, job_ids = submit_bands(runners[chosen.account], plans, config, items, stamp=tag)
    for band, job_id in zip(bands, job_ids, strict=True):
        band['job_id'] = job_id
    whole = replace(
        plans[0],
        shards=tuple(s for p in plans for s in p.shards),
        minutes=max(p.minutes for p in plans),
        mem_mb=max(p.mem_mb for p in plans),
        throttle=sum(p.throttle for p in plans),
        makespan_minutes=max(p.makespan_minutes for p in plans),
    )
    record = {**_plan_record(whole, chosen, job_ids, run_dir), 'bands': bands, 'unmeasured': sorted(unmeasured)}
    state['rounds'].append(record)
    state['current'] = {
        'account': chosen.account,
        'tag': tag,
        'items': items,
        'job_ids': job_ids,
        'shards': len(whole.shards),
        'bands': bands,
        'ceiling': grantable is not None,  # an OOM round is not counted against RETRIES while a ceiling exists
    }


def _rewalled(
    plan: Plan, shards: tuple[tuple[int, int], ...], seconds: list[float], policy: Policy, min_minutes: float
) -> Plan:
    """*plan* over the balanced *shards*: its wall the slowest padded shard, never under *min_minutes*.

    Never longer than the plan's own wall, which the partition ceiling already held.
    """
    slowest = max(sum(seconds[a:b]) for a, b in shards) * policy.safety
    minutes = min(plan.minutes, max(1, math.ceil(slowest / 60), math.ceil(min_minutes)))
    waves = plan.makespan_minutes // plan.minutes
    return replace(plan, shards=shards, minutes=minutes, makespan_minutes=waves * minutes)


def submit_run(
    spec: RunSpec,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    cost: Cost,
    policy: Policy,
    stamp: str | None = None,
    history: dict[str, Any] | None = None,
    carry: Callable[[str], Mapping[str, tuple[str, str, str]]] | None = None,
) -> dict[str, Any]:
    """Fetch, build, collect, plan and submit round 0; leave the run's state ON THE CLUSTER. Returns that state.

    The state is ``~/ci/runs/<sha>/<part>/state.json``; :func:`lab_commons.hpc.gather.gather_run` reads it.
    *history* is a previous record, read for its measurements only. Nothing here waits for a job.

    *carry*, given the env key the build reported, names the cells earlier runs of THAT env hold for
    ids whose cell cannot have changed (``{node: (plain cell, origin sha, run sha)}``,
    :func:`dev_lab.dev.forgecarry.carry_from`). Those of the ids collected here are recorded with their
    origin in ``carried`` and never submitted; when nothing is left the record is written at once.
    """
    runners = {g.account: connect(g) for g in machine.grants}
    snapshots = [(g, probe(runners[g.account], g.slurm_account)) for g in machine.grants]
    grant, _ = allocate(
        1, cost, snapshots, session=machine.session, policy=policy, paging=machine.paging, rerun_safe=True
    )
    run = runners[grant.account]
    host = grant.hosts[0]
    told = build_run(spec, run, f'{grant.name} ({host})')
    collected = run(collect_command(spec), None)
    ids, errored = parse_ids(collected), parse_collection_errors(collected)
    if spec.only is not None:
        ids = [node for node in ids if node in spec.only]
        errored = [node for node in errored if any(n.split('::')[0] == node for n in spec.only)]
    if not ids:
        raise NotSubmitted(nothing_collected(spec, collected))
    carrying = {name: set(parse_ids(run(collect_command(spec, also=name), None))) for name in spec.vocabulary.resources}
    handed = {node: owner for node, owner in sorted(need_sets(ids, carrying).items()) if owner != spec.part}
    broken = TestCell(Cell(Status.REJECTED, reason=Reason('collection', 'the file did not collect'))).to_plain()
    cells: dict[str, dict[str, Any]] = dict.fromkeys(errored, broken)
    present = set(ids)
    offered = carry(told['env']) if carry else {}
    taken = {
        node: held for node, held in offered.items() if node in present and node not in cells and node not in handed
    }
    cells.update({node: held[0] for node, held in taken.items()})
    state: dict[str, Any] = {
        'sha': spec.sha,
        'repo': spec.repo_url,
        'stamp': stamp or time.strftime('%Y%m%d-%H%M%S'),
        'scope': spec.scope,
        'timeout_s': spec.timeout_s,
        'platform': spec.part,
        'system': told.get('platform', ''),
        'env': told['env'],
        'resolved': json.loads(told.get('resolved') or '[]'),  # every name==version it ran against (envkey)
        'builds': {'env': spec.builds.env_key, 'native': spec.builds.native_key},  # ~/ci/envs, ~/ci/native keys
        'cluster': host,
        'python': told.get('python', ''),
        'prep': {**told['prep'], **parse_times(collected)},
        'handed': handed,
        'cells': cells,
        'measured': Measured.of(history or {}).record(),
        'rounds': [],
        'grown': {},  # file -> this run's memory growth after its item was OOM-killed
        'carried': {node: held[1] for node, held in taken.items()},
    }
    files = group_items([node for node in ids if node not in handed and node not in taken])
    measured = Measured.of(state['measured'])

    def band(ids: list[str]) -> int:
        return learned.memory_band(measured.item_gb(ids, cost, policy))

    pending = measured.pack(files, cost, item_seconds(policy), band)
    if not pending:  # every id was carried or handed: the record needs no job
        state['record'] = record_of(state, grant.slurm_account)
        save_state(run, state)
        return state
    same_cluster = [(g, s) for g, s in snapshots if shares_home(g, grant)]
    submit_round(state, pending, runners=runners, snapshots=same_cluster, machine=machine, cost=cost, policy=policy)
    save_state(runners[state['current']['account']], state)
    return state


def cancel_run(
    sha: str,
    part: str,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    also: Sequence[str] = (),
    aliases: Sequence[str] = (),
) -> list[str]:
    """``scancel`` every recorded round of *sha*'s run of *part*, *also*, and any job of its name Slurm holds.

    THE DELIBERATE STOP (a user's or integrator's): started tasks die too. Supersession never calls it
    (:func:`withdraw_pending`).
    """
    shell, _names, ids = _run_jobs(sha, part, machine, connect, also=also, aliases=aliases)
    if ids:
        _scancel(shell, ids)
    return ids


def withdraw_pending(
    sha: str,
    part: str,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    also: Sequence[str] = (),
    aliases: Sequence[str] = (),
) -> list[str]:
    """Withdraw every NOT-YET-STARTED task of *sha*'s run of *part*; the job ids still started after it.

    NEWEST-HEAD-ONLY (user ruling 2026-10-22): a superseded run loses its pending tasks and keeps its started
    ones -- ``scancel --state=PENDING`` drops only an array's pending elements, never a running one. ``[]``
    means nothing of the run is left running.
    """
    shell, names, ids = _run_jobs(sha, part, machine, connect, also=also, aliases=aliases)
    if not ids:
        return []
    _scancel(shell, ids, '--state=PENDING')
    return sorted(set(shell(f'squeue -h --me -n {names} -t {_STARTED} -o %F', None).split()))


#: ``scancel``'s line for an id Slurm no longer holds: a job already gone is what a cancel asks for.
_GONE: Final = 'Invalid job id specified'


def _scancel(shell: Runner, ids: Sequence[str], *flags: str) -> None:
    """``scancel`` *ids*; an id Slurm no longer holds is not a refusal (Ada 2026-10-23), any other line is.

    ``scancel`` still signals every id it holds when one of them is gone, and exits 1 for the gone one.
    """
    try:
        shell(shlex.join(('scancel', *flags, *ids)), None)
    except RuntimeError as refused:
        errors = [line for line in str(refused).splitlines() if 'error' in line]
        if not errors or any(_GONE not in line for line in errors):
            raise


#: The squeue states of a task that has STARTED: supersession never cancels one.
_STARTED: Final = 'RUNNING,COMPLETING,CONFIGURING,SUSPENDED'


def _run_jobs(
    sha: str,
    part: str,
    machine: Machine,
    connect: Callable[[Grant], Runner],
    *,
    also: Sequence[str],
    aliases: Sequence[str],
) -> tuple[Runner, str, list[str]]:
    """The shell, the job names, and every job id of the run: each recorded round, *also*, and squeue's by name.

    Names are its :func:`job_name` and *aliases*; squeue's ``%F`` is the array's own id (``%i`` spells a
    pending array ``7683032_[12-68%1]``, which ``scancel`` refuses: Ada 2026-10-14).
    """
    try:
        state, _ = load_state(sha, part, machine, {grant.account: connect(grant) for grant in machine.grants})
        recorded = [job for one in state['rounds'] for job in one['job_ids']]
    except Unreachable:
        raise
    except RuntimeError:  # no state.json: only what Slurm holds by name
        recorded = []
    shell = connect(machine.grants[0])
    names = ','.join((job_name(sha, part), *aliases))
    held = shell(f'squeue -h --me -n {names} -o %F', None).split()
    return shell, names, sorted({*recorded, *also, *held})


def job_name(sha: str, part: str) -> str:
    """The Slurm job name of the run of *part* on *sha*: another part of the sha never takes it for its own."""
    return f'run-{sha[:12]}-{part}'


def load_state(sha: str, part: str, machine: Machine, runners: dict[str, Runner]) -> tuple[dict[str, Any], Runner]:
    """The state of *sha*'s run of *part* from whichever grant holds it; :class:`Unreachable` when none answered."""
    answered = False
    for grant in machine.grants:
        try:
            text = runners[grant.account](f'cat {_state_dir(sha, part)}/state.json 2>/dev/null || true', None)
        except Unreachable:
            continue
        answered = True
        if text.strip():  # live state: a run in flight across a change is upgraded on read
            return upgraded_state(json.loads(text)), runners[grant.account]
    if not answered:
        msg = f'no login host of any grant answered; the jobs of {sha[:12]} keep running -- gather again later'
        raise Unreachable(msg)
    msg = f'no run of {sha} ({part}) was submitted on any grant (no {_state_dir(sha, part)}/state.json)'
    raise RuntimeError(msg)


def _plan_record(plan: Plan, grant: Grant, job_ids: list[str], run_dir: str) -> dict[str, Any]:
    return {**asdict(plan), 'grant': grant.name, 'job_ids': job_ids, 'run_dir': run_dir}
