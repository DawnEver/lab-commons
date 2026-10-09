"""``python -m lab_commons.hpc <verb>`` -- probe, plan, submit, status, gather, retry, verdict {submit|gather}.

WHERE comes from this machine's grants (:mod:`lab_commons.hpc.grants`, the ``[hpc]`` table of
:mod:`lab_commons.config`); WHAT from the job file ``-c`` (:mod:`lab_commons.hpc.config`). The last
submission of a job file is recorded next to it as ``<job>.run.json`` (the grant it went to and its ssh
targets, run directory, every job id it took, shard count), so ``status``/``gather``/``retry`` need
nothing more -- through whichever of those login hosts answers.

``verdict submit`` tests one commit on the cluster (:mod:`lab_commons.hpc.verdict`) and returns once the
first round is queued; ``verdict gather --sha <sha> -o <record>`` is one short call -- pending (exit
:data:`PENDING`), or the record written to ``-o``. ``-c`` there is optional and only its
``[cost]``/``[policy]`` are read. ``--history`` names a previous verdict record whose measured
durations, overheads and peaks plan this one; without it every file is priced at ``[cost]``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lab_commons.hpc.builds import builds_at
from lab_commons.hpc.config import Config, Limits, load_config
from lab_commons.hpc.grants import Grant, Machine, load_grants
from lab_commons.hpc.plan import Plan, allocate, free_slots, headroom, quota_slots
from lab_commons.hpc.platforms import table_at
from lab_commons.hpc.run import (
    TERMINAL_OK,
    Runner,
    Unreachable,
    failover_runner,
    gather,
    probe,
    runner_for,
    shard_states,
    ssh_runner,
    submit,
)
from lab_commons.hpc.slurm import Snapshot
from lab_commons.hpc.verdict import VerdictSpec, gather_verdict, submit_verdict, summary, write_record
from lab_commons.log import emit

__all__ = ['main']

type Connect = Callable[[Grant], Runner]

#: A login-node build of a tree's venv (a Rust extension, say) needs far more than a probe's five minutes.
_BUILD_TIMEOUT = 3 * 3600.0


def _state_path(config: Config) -> Path:
    if config.source is None:
        msg = 'a run is recorded next to its job file; this config was not loaded from one'
        raise ValueError(msg)
    return config.source.with_suffix('.run.json')


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
        _out(f'  shares known here {here} of quota {q.cpus}; held {held}; headroom for {machine.workstation}: {room}')
        _out(f'  one item ({config.cost}) -> {quota_slots(snapshot, config.cost, Limits(cpus=room))} concurrent')
        for name in grant.partitions or tuple(snapshot.partitions):
            part = snapshot.partitions.get(name)
            if part is not None:
                _out(
                    f'    {name:<14} max {part.max_minutes} min, '
                    f'{free_slots(snapshot, name, config.cost)} item slots free'
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


#: Exit status of a ``verdict gather`` that wrote no record yet: still running, re-submitted, or unreachable.
PENDING = 3


def _only(path: Path | None) -> frozenset[str] | None:
    """The ids an ``--only`` file selects, one per line; ``None`` runs everything collected."""
    if path is None:
        return None
    return frozenset(line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip())


def _verdict_submit(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    spec = VerdictSpec(
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
    run_id = submit_verdict(
        spec,
        machine,
        lambda g: runner_for(g, _BUILD_TIMEOUT),
        cost=config.cost,
        policy=config.policy,
        history=json.loads(args.history.read_text(encoding='utf-8')) if args.history else None,
    )
    _out(f'submitted verdict {run_id}; gather it: python -m lab_commons.hpc verdict gather --sha {run_id} -o <record>')
    return 0


def _verdict_gather(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    try:
        record = gather_verdict(args.sha, machine, runner_for, cost=config.cost, policy=config.policy)
    except Unreachable as exc:
        _out(f'pending: {exc}')
        return PENDING
    if record is None:
        _out(f'pending: {args.sha[:12]} is still running on the cluster -- gather again later')
        return PENDING
    write_record(record, args.output)
    _out(f'{args.sha[:12]} on {record["cluster"]} ({record["platform"]}, python {record["python"]}): {summary(record)}')
    _out(f'written {args.output}')
    _out(
        f'record it as the linux part: python -m lab_commons.dev.platformparts record-linux {args.output} --tier <tier>'
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc', description=__doc__.splitlines()[0])
    parser.add_argument('verb', choices=['probe', 'plan', 'submit', 'status', 'gather', 'retry', 'verdict'])
    parser.add_argument('step', nargs='?', choices=['submit', 'gather'], help='verdict only: submit, then gather')
    parser.add_argument('-c', '--config', type=Path, help='the job file (default hpc.toml; optional for verdict)')
    parser.add_argument('-o', '--output', type=Path, help='gather: merged item results (JSON) or the verdict record')
    verdict = parser.add_argument_group('verdict')
    verdict.add_argument('--sha', help='the full commit id to test')
    verdict.add_argument('--repo-url', help='read-only HTTPS remote the cluster fetches from')
    verdict.add_argument('--install', help='shell, run once in the tree with its fresh .venv active')
    verdict.add_argument('--collect', default='', help='pytest arguments selecting the tests (paths, -m ...)')
    verdict.add_argument('--select', default='', help='marker expression choosing the tests (one -m, joined)')
    verdict.add_argument('--python', default='', help='interpreter request for `uv venv --python`')
    verdict.add_argument(
        '--repo',
        type=Path,
        default=Path.cwd(),
        help='local repository, READ only: its [tool.lab_commons.platforms] at --sha; a pack if the remote lacks it',
    )
    verdict.add_argument(
        '--needs', default='', help='commands the login node must have beyond git and uv, comma-separated (cargo,cc)'
    )
    verdict.add_argument('--only', type=Path, help='a file of node ids, one per line: run just these (incremental)')
    verdict.add_argument('--history', type=Path, help='a previous verdict record: its measurements plan this run')
    return parser


_VERDICT_NEEDS = {
    'submit': ('--sha', '--repo-url', '--install'),
    'gather': ('--sha', '--output'),
}


def main(argv: list[str] | None = None) -> int:
    """Parse the verb and dispatch it against this machine's grants."""
    parser = _parser()
    args = parser.parse_args(argv)
    machine = load_grants()
    if args.verb == 'verdict':
        if args.step is None:
            parser.error('verdict needs a step: submit, then gather')
        values = {'--sha': args.sha, '--repo-url': args.repo_url, '--install': args.install, '--output': args.output}
        missing = [flag for flag in _VERDICT_NEEDS[args.step] if not values[flag]]
        if missing:
            parser.error(f'verdict {args.step} needs {", ".join(missing)}')
        config = load_config(args.config) if args.config else Config()
        step = _verdict_submit if args.step == 'submit' else _verdict_gather
        return step(args, machine, config)
    if args.step is not None:
        parser.error(f'{args.verb} takes no step')
    config = load_config(args.config or Path('hpc.toml'))
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
