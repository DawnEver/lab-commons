"""``python -m lab_commons.hpc <verb> -c hpc.toml`` -- probe, plan, submit, status, gather, retry.

The last submission of a config is recorded next to it as ``<config>.run.json`` (run directory, every
job id it took, shard count), so ``status``/``gather``/``retry`` need no argument beyond the config.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lab_commons.hpc.config import Config, load_config
from lab_commons.hpc.plan import Plan, free_slots, make_plan, quota_slots
from lab_commons.hpc.run import TERMINAL_OK, Runner, gather, probe, runner_for, shard_states, submit
from lab_commons.log import emit

__all__ = ['main']


def _state_path(config: Config) -> Path:
    assert config.source is not None  # noqa: S101 -- load_config always sets it
    return config.source.with_suffix('.run.json')


def _items(config: Config) -> list[Any]:
    items = json.loads(config.items_path().read_text(encoding='utf-8'))
    if not isinstance(items, list):
        msg = f'{config.items_path()} must hold a JSON list'
        raise TypeError(msg)
    return items


def _plan(config: Config, run: Runner) -> Plan:
    snapshot = probe(run, config.cluster.account)
    return make_plan(
        len(_items(config)), config.cost, snapshot, cluster=config.cluster, policy=config.policy, limits=config.limits
    )


def _out(text: str) -> None:
    emit(text)


def _probe(config: Config, run: Runner) -> int:
    snapshot = probe(run, config.cluster.account)
    q = snapshot.quota
    _out(f'account {q.account} (qos {q.default_qos}): free quota cpus={q.cpus} mem_mb={q.mem_mb} gpus={q.gpus}')
    _out(f'one item ({config.cost}) -> {quota_slots(snapshot, config.cost, config.limits)} concurrent by quota')
    for name in config.cluster.partitions or tuple(snapshot.partitions):
        part = snapshot.partitions.get(name)
        if part is not None:
            _out(f'  {name:<14} max {part.max_minutes} min, {free_slots(snapshot, name, config.cost)} item slots free')
    return 0


def _submit(config: Config, run: Runner) -> int:
    plan = _plan(config, run)
    _out(plan.describe())
    stamp = time.strftime('%Y%m%d-%H%M%S')
    sub = submit(run, plan, config, _items(config), stamp=stamp)
    record = {
        'run_dir': sub.run_dir,
        'stamp': stamp,
        'job_ids': [sub.job_id],
        'shards': sub.shards,
        'plan': asdict(plan),
    }
    _state_path(config).write_text(json.dumps(record, indent=2), encoding='utf-8')
    _out(f'submitted job {sub.job_id}; run directory {sub.run_dir}')
    return 0


def _record(config: Config) -> dict[str, Any]:
    return json.loads(_state_path(config).read_text(encoding='utf-8'))


def _status(config: Config, run: Runner) -> int:
    record = _record(config)
    states = shard_states(run, record['job_ids'])
    counts: dict[str, int] = {}
    for shard in range(record['shards']):
        state = states.get(shard, 'UNKNOWN')
        counts[state] = counts.get(state, 0) + 1
    _out(f'{record["run_dir"]}: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    return 0


def _gather(config: Config, run: Runner, target: Path | None) -> int:
    record = _record(config)
    results = gather(run, record['run_dir'])
    flat = sorted((r for shard in results.values() for r in shard), key=lambda r: r['index'])
    failed = sum(not r['ok'] for r in flat)
    _out(f'{len(results)}/{record["shards"]} shards returned, {len(flat)} items, {failed} item errors')
    if target is not None:
        target.write_text(json.dumps(flat), encoding='utf-8')
        _out(f'written {target}')
    return 0


def _retry(config: Config, run: Runner) -> int:
    record = _record(config)
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


def main(argv: list[str] | None = None) -> int:
    """Parse the verb and dispatch it against the config's cluster."""
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc', description=__doc__.splitlines()[0])
    parser.add_argument('verb', choices=['probe', 'plan', 'submit', 'status', 'gather', 'retry'])
    parser.add_argument('-c', '--config', type=Path, default=Path('hpc.toml'))
    parser.add_argument('-o', '--output', type=Path, help='gather: write the merged item results here (JSON)')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    run = runner_for(config)
    if args.verb == 'probe':
        return _probe(config, run)
    if args.verb == 'plan':
        _out(_plan(config, run).describe())
        return 0
    if args.verb == 'submit':
        return _submit(config, run)
    if args.verb == 'status':
        return _status(config, run)
    if args.verb == 'gather':
        return _gather(config, run, args.output)
    return _retry(config, run)


if __name__ == '__main__':
    sys.exit(main())
