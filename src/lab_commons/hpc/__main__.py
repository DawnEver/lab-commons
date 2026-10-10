"""``python -m lab_commons.hpc <verb>`` -- probe, plan, submit, status, gather, retry, run {submit|gather|watch|status}.

WHERE comes from this machine's grants (:mod:`lab_commons.hpc.grants`, the ``[hpc]`` table of
:mod:`lab_commons.config`); WHAT from the job file ``-c`` (:mod:`lab_commons.hpc.config`). The last
submission of a job file is recorded next to it as ``<job>.submission.json`` (the grant it went to and its
ssh targets, run directory, every job id it took, shard count), so ``status``/``gather``/``retry`` need
nothing more -- through whichever of those login hosts answers.

``run submit --scope full|incremental`` tests one commit on the cluster (:mod:`lab_commons.hpc.run`) and
returns once the first round is queued; ``incremental`` names its re-run ids with ``--only``, ``full``
takes none; it records the run in :mod:`lab_commons.hpc.outstanding`, the machine's one list.
``run gather --sha <sha> -o <dir>`` is one short call -- where the run is and exit :data:`PENDING`, or the
record written to ``<dir>/run-<sha>-<platform>-<req>.json.gz`` (exit 1 if any id is red, lost or
missing); ``--wait`` blocks until the record. ``run watch -o <dir>`` is the same path over every
outstanding run -- one call any harness tick can make. ``run status`` says where each outstanding run is
(queued with Slurm's reason, running k of n, done) and names a stall with its remedy. ``probe`` needs no
job file. ``-c`` there is optional and only its
``[cost]``/``[policy]`` are read. ``--history`` names a previous run record whose measured durations,
overheads and peaks plan this one; without it every file is priced at ``[cost]``. A retired verb is
refused by name (:mod:`lab_commons.hpc.retired`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc import outstanding
from lab_commons.hpc.builds import builds_at
from lab_commons.hpc.cluster import (
    TERMINAL_OK,
    Runner,
    failover_runner,
    gather,
    probe,
    runner_for,
    shard_states,
    ssh_runner,
    submit,
)
from lab_commons.hpc.config import Config, Limits, load_config
from lab_commons.hpc.grants import Grant, Machine, load_grants
from lab_commons.hpc.plan import Plan, allocate, free_slots, headroom, quota_slots
from lab_commons.hpc.platforms import table_at
from lab_commons.hpc.records import read_record
from lab_commons.hpc.retired import refusal
from lab_commons.hpc.run import submit_run
from lab_commons.hpc.shell import FULL, INCREMENTAL, RunSpec
from lab_commons.hpc.slurm import Snapshot
from lab_commons.hpc.watch import settle, status
from lab_commons.log import emit

__all__ = ['main']

type Connect = Callable[[Grant], Runner]

#: A login-node build of a tree's venv (a Rust extension, say) needs far more than a probe's five minutes.
_BUILD_TIMEOUT = 3 * 3600.0


def _state_path(config: Config) -> Path:
    if config.source is None:
        msg = 'a submission is recorded next to its job file; this config was not loaded from one'
        raise ValueError(msg)
    return config.source.with_suffix('.submission.json')


def _items(config: Config) -> list[Any]:
    items = json.loads(config.items_path().read_text(encoding='utf-8'))
    if not isinstance(items, list):
        msg = f'{config.items_path()} must hold a JSON list'
        raise TypeError(msg)
    return items


def _snapshots(machine: Machine, connect: Connect) -> list[tuple[Grant, Snapshot]]:
    return [(g, probe(connect(g), g.slurm_account)) for g in machine.grants]


def _out(text: str) -> None:
    emit(text)


def _probe(config: Config, machine: Machine, connect: Connect) -> int:
    snapshots = _snapshots(machine, connect)
    for grant, snapshot in snapshots:
        q = snapshot.quota
        here = sum(g.cpus for g, _ in snapshots if g.account == grant.account and g.same_cluster(grant))
        held = ', '.join(f'{tag or "untagged"}={cpus:g}' for tag, cpus in sorted(snapshot.usage.items())) or 'nothing'
        room = headroom(snapshot, grant.cpus, machine.workstation)
        _out(
            f'{grant.name} account {q.account} (qos {q.default_qos}): '
            f'quota cpus={q.cpus} mem_mb={q.mem_mb} gpus={q.gpus}'
        )
        box = machine.workstation or 'this unnamed box'
        _out(f'  shares known here {here} of quota {q.cpus}; held {held}; headroom for {box}: {room}')
        _out(f'  one item ({config.cost}) -> {quota_slots(snapshot, config.cost, Limits(cpus=room))} concurrent')
        for name in grant.partitions or tuple(snapshot.partitions):
            part = snapshot.partitions.get(name)
            if part is not None:
                _out(
                    f'    {name:<14} max {part.max_minutes} min, '
                    f'{free_slots(snapshot, (name,), config.cost)} item slots free'
                )
    return 0


def _allocate(config: Config, machine: Machine, connect: Connect) -> tuple[Grant, Plan]:
    return allocate(
        len(_items(config)),
        config.cost,
        _snapshots(machine, connect),
        workstation=machine.workstation,
        policy=config.policy,
    )


def _plan(config: Config, machine: Machine, connect: Connect) -> int:
    grant, plan = _allocate(config, machine, connect)
    _out(f'{grant.name}: {plan.describe()}')
    return 0


def _submit(config: Config, machine: Machine, connect: Connect) -> int:
    grant, plan = _allocate(config, machine, connect)
    _out(f'{grant.name}: {plan.describe()}')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    sub = submit(connect(grant), plan, config, _items(config), stamp=stamp)
    record = {
        'grant': grant.name,
        'targets': list(grant.targets),
        'run_dir': sub.run_dir,
        'stamp': stamp,
        'job_ids': [sub.job_id],
        'shards': sub.shards,
        'plan': asdict(plan),
    }
    _state_path(config).write_text(json.dumps(record, indent=2), encoding='utf-8')
    _out(f'submitted job {sub.job_id} to {grant.name}; run directory {sub.run_dir}')
    return 0


def _record(config: Config) -> dict[str, Any]:
    return json.loads(_state_path(config).read_text(encoding='utf-8'))


def _status(run: Runner, record: dict[str, Any]) -> int:
    states = shard_states(run, record['job_ids'])
    counts: dict[str, int] = {}
    for shard in range(record['shards']):
        state = states.get(shard, 'UNKNOWN')
        counts[state] = counts.get(state, 0) + 1
    _out(f'{record["run_dir"]}: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    return 0


def _gather(run: Runner, record: dict[str, Any], target: Path | None) -> int:
    results = gather(run, record['run_dir'])
    flat = sorted((r for shard in results.values() for r in shard), key=lambda r: r['index'])
    failed = sum(not r['ok'] for r in flat)
    _out(f'{len(results)}/{record["shards"]} shards returned, {len(flat)} items, {failed} item errors')
    if target is not None:
        target.write_text(json.dumps(flat), encoding='utf-8')
        _out(f'written {target}')
    return 0


def _retry(config: Config, run: Runner, record: dict[str, Any]) -> int:
    states = shard_states(run, record['job_ids'])
    returned = gather(run, record['run_dir'])
    redo = [s for s in range(record['shards']) if s not in returned and states.get(s) not in {'PENDING', 'RUNNING'}]
    if not redo:
        _out('nothing to retry')
        return 0
    plan = Plan(**{**record['plan'], 'shards': tuple(map(tuple, record['plan']['shards']))})
    sub = submit(run, plan, config, [], stamp=record['stamp'], only=redo)
    record['job_ids'].append(sub.job_id)
    _state_path(config).write_text(json.dumps(record, indent=2), encoding='utf-8')
    _out(f'resubmitted {len(redo)} shards as job {sub.job_id} (not {TERMINAL_OK} and no results file)')
    return 0


def _only(path: Path | None) -> frozenset[str] | None:
    """The ids an ``--only`` file selects, one per line; ``None`` runs everything collected."""
    if path is None:
        return None
    return frozenset(line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip())


def _run_submit(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    spec = RunSpec(
        sha=args.sha,
        repo_url=args.repo_url,
        install=args.install,
        collect=args.collect,
        select=args.select,
        table=table_at(args.repo, args.sha),
        python=args.python,
        pack_from=args.repo,
        builds=builds_at(args.repo, args.sha, install=args.install, python=args.python),
        only=_only(args.only),
        needs=tuple(filter(None, args.needs.split(','))),
    )
    state = submit_run(
        spec,
        machine,
        lambda g: runner_for(g, _BUILD_TIMEOUT),
        cost=config.cost,
        policy=config.policy,
        history=read_record(args.history) if args.history else None,
    )
    outstanding.add(
        spec.sha,
        repo=spec.repo_url,
        grant=state['rounds'][0]['grant'],
        job_ids=[r['job_id'] for r in state['rounds']],
    )
    _out(
        f'submitted run {spec.sha} ({spec.scope}); wait for it: python -m lab_commons.hpc run watch -o <dir> '
        f'(or run gather --sha {spec.sha} -o <dir> [--wait])'
    )
    return 0


#: ``time.sleep``, named so a test drives the poll loop without waiting.
_sleep = time.sleep


def _run_gather(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    return settle([args.sha], machine, runner_for, config, args.output, sleep=_sleep if args.wait else None)


def _run_watch(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    shas = [entry['sha'] for entry in outstanding.outstanding()]
    if not shas:
        _out(f'no outstanding run ({outstanding.directory()} is empty)')
        return 0
    return settle(shas, machine, runner_for, config, args.output, sleep=_sleep)


def _run_status(_args: argparse.Namespace, machine: Machine, config: Config) -> int:
    return status(machine, runner_for, config)


#: Each ``run`` step and the flags it cannot do without.
_RUN_NEEDS: Final = {
    'submit': ('--sha', '--repo-url', '--install', '--scope'),
    'gather': ('--sha', '--output'),
    'watch': ('--output',),
    'status': (),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc', description=__doc__.splitlines()[0])
    parser.add_argument('verb', choices=['probe', 'plan', 'submit', 'status', 'gather', 'retry', 'run'])
    parser.add_argument(
        'step', nargs='?', choices=list(_RUN_NEEDS), help='run only: submit, then gather/watch; status lists them'
    )
    parser.add_argument('-c', '--config', type=Path, help='the job file; probe and run need none ([cost]/[policy])')
    parser.add_argument(
        '-o', '--output', type=Path, help='gather: merged item results (JSON); run gather/watch: the record directory'
    )
    parser.add_argument('--wait', action='store_true', help='run gather: block until the run has its record')
    group = parser.add_argument_group('run')
    group.add_argument('--scope', choices=[FULL, INCREMENTAL], help='run submit: every id, or only --only')
    group.add_argument('--sha', help='the full commit id to test')
    group.add_argument('--repo-url', help='read-only HTTPS remote the cluster fetches from')
    group.add_argument('--install', help='shell, run once in the tree with its fresh .venv active')
    group.add_argument('--collect', default='', help='pytest arguments selecting the tests (paths, -m ...)')
    group.add_argument('--select', default='', help='marker expression choosing the tests (one -m, joined)')
    group.add_argument('--python', default='', help='interpreter request for `uv venv --python`')
    group.add_argument(
        '--repo',
        type=Path,
        default=Path.cwd(),
        help='local repository, READ only: its [tool.lab_commons.platforms] at --sha; a pack if the remote lacks it',
    )
    group.add_argument(
        '--needs', default='', help='commands the login node must have beyond git and uv, comma-separated (cargo,cc)'
    )
    group.add_argument('--only', type=Path, help='a file of node ids, one per line: run just these (incremental)')
    group.add_argument('--history', type=Path, help='a previous run record: its measurements plan this run')
    return parser


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """``run <step>``: check the step's flags, then dispatch it."""
    if args.step is None:
        parser.error('run needs a step: submit, then gather or watch; status lists what is outstanding')
    if args.wait and args.step != 'gather':
        parser.error('--wait belongs to run gather; run watch always waits')
    values = {
        '--sha': args.sha,
        '--repo-url': args.repo_url,
        '--install': args.install,
        '--output': args.output,
        '--scope': args.scope,
    }
    missing = [flag for flag in _RUN_NEEDS[args.step] if not values[flag]]
    if missing:
        parser.error(f'run {args.step} needs {", ".join(missing)}')
    if args.step == 'submit' and (args.scope == INCREMENTAL) != (args.only is not None):
        parser.error('--scope incremental names its re-run ids with --only; --scope full takes no --only')
    config = load_config(args.config) if args.config else Config()
    steps = {'submit': _run_submit, 'gather': _run_gather, 'watch': _run_watch, 'status': _run_status}
    return steps[args.step](args, load_grants(), config)


def _job_config(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Config:
    """The job file ``-c`` names; ``probe`` alone may go without one. Never a guessed file in the cwd."""
    if args.config is None:
        if args.verb != 'probe':
            parser.error(
                f'{args.verb} reads a job file and the submission recorded beside it; pass -c <job file> '
                '(the one submit used). A commit test is `run submit`/`run gather --sha`, which need none.'
            )
        return Config()
    if not args.config.is_file():
        parser.error(f'no job file at {args.config}; pass -c <job file> naming an existing one')
    return load_config(args.config)


def main(argv: list[str] | None = None) -> int:
    """Parse the verb and dispatch it against this machine's grants."""
    parser = _parser()
    argv = sys.argv[1:] if argv is None else argv
    if argv and (refused := refusal('lab_commons.hpc', argv[0])):
        parser.error(refused)
    args = parser.parse_args(argv)
    if args.verb == 'run':
        return _run(parser, args)
    machine = load_grants()
    if args.step is not None:
        parser.error(f'{args.verb} takes no step')
    config = _job_config(parser, args)
    connect = runner_for
    if args.verb == 'probe':
        return _probe(config, machine, connect)
    if args.verb == 'plan':
        return _plan(config, machine, connect)
    if args.verb == 'submit':
        return _submit(config, machine, connect)
    record = _record(config)
    run = failover_runner([(t, ssh_runner(t)) for t in record['targets']])
    tracked = {
        'status': lambda: _status(run, record),
        'gather': lambda: _gather(run, record, args.output),
        'retry': lambda: _retry(config, run, record),
    }
    return tracked[args.verb]()


if __name__ == '__main__':
    sys.exit(main())
