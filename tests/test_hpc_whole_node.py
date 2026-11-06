"""A whole-node task: ``[task] exclusive`` and ``lanes`` -- one array task, one node, ``lanes`` items at once.

Each grant is a fake login node answering the recorded cluster (``_hpc_fixtures``); nothing reaches a cluster.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lab_commons.hpc import jobs
from lab_commons.hpc.cluster import render_script, submit
from lab_commons.hpc.config import Config, Cost, JobSpec, Task, load_config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import Plan, Unplaceable
from lab_commons.hpc.worker import RESULTS, run_shard

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n@@@ shared\n'
#: shortq and defq hold only 96-core nodes in the recorded cluster.
NODE96 = Grant(user='me', hosts=('near',), slurm_account='acct-free', partitions=('shortq', 'defq'))
SHARD = Cost(cpus=12, mem_gb=40, seconds=600)


def _login(command: str, stdin: str | None = None) -> str:
    del stdin
    if 'scontrol show node' in command:
        return IDLE
    if 'sbatch' in command:
        return '42\n'
    return ''


def _config(task: Task, cost: Cost = SHARD) -> Config:
    return Config(job=JobSpec(name='relay', workdir='~/p', entry='m:f'), cost=cost, task=task)


def _plan(config: Config, n_items: int) -> Plan:
    machine = Machine(session=None, grants=(NODE96,))
    (placement,) = jobs.plan_job(config, n_items, machine, lambda _g: _login)
    assert placement.items == (0, n_items)
    return placement.plan


def test_the_task_section_loads_and_scales_one_item_to_the_task(tmp_path: Path) -> None:
    path = tmp_path / 'job.toml'
    path.write_text('[cost]\ncpus = 12\nmem_gb = 40\n[task]\nexclusive = true\nlanes = 8\n', encoding='utf-8')
    config = load_config(path)
    assert config.task == Task(exclusive=True, lanes=8)
    assert config.task.cost(config.cost) == Cost(cpus=96, mem_gb=320, seconds=60)
    with pytest.raises(ValueError, match='lanes'):
        Task(lanes=0)


def test_a_whole_node_task_is_charged_the_whole_node_and_packs_lanes_items_a_shard() -> None:
    plan = _plan(_config(Task(exclusive=True, lanes=8)), 20)
    assert (plan.cpus, plan.mem_mb, plan.exclusive, plan.lanes) == (96, 8 * 40 * 1024, True, 8)
    assert plan.throttle == 1, 'acct-free holds cpu=96: one whole node at a time'
    assert plan.shards[0][0] == 0
    assert plan.shards[-1][1] == 20
    assert all(a % 8 == 0 for a, _ in plan.shards), 'a shard starts on a lane boundary'
    rounds = max(-(-(b - a) // 8) for a, b in plan.shards)
    assert plan.minutes == rounds * 15, 'each round of 8 lanes is one item: 600 s x 1.5'
    # Planted control: the same items one lane a task are planned per item, never exclusive.
    narrow = _plan(_config(Task()), 20)
    assert (narrow.cpus, narrow.exclusive, narrow.lanes) == (12, False, 1)


def test_an_exclusive_task_narrower_than_its_nodes_is_refused() -> None:
    """Slurm charges an exclusive task the whole node: a plan priced at less would overrun the quota."""
    with pytest.raises(Unplaceable, match='96-CPU node'):
        _plan(_config(Task(exclusive=True, lanes=4)), 8)


def test_the_script_asks_for_the_node_and_threads_each_lane_to_its_item() -> None:
    config = _config(Task(exclusive=True, lanes=8))
    script = render_script(_plan(config, 16), config, '~/p/.lab-hpc/relay-t0')
    assert '#SBATCH --exclusive\n' in script
    assert '#SBATCH --cpus-per-task=96\n' in script
    assert 'OMP_NUM_THREADS=12 ' in script
    narrow = _config(Task())
    assert '--exclusive' not in render_script(_plan(narrow, 16), narrow, '~/p/.lab-hpc/relay-t0')


def test_the_manifest_carries_the_lanes() -> None:
    calls: list[tuple[str, str | None]] = []

    def login(command: str, stdin: str | None = None) -> str:
        calls.append((command, stdin))
        return _login(command, stdin)

    config = _config(Task(exclusive=True, lanes=8))
    submit(login, _plan(config, 16), config, list(range(16)), stamp='t0')
    assert json.loads(calls[0][1] or '')['lanes'] == 8


def test_the_worker_runs_a_shards_items_lanes_at_once(tmp_path: Path) -> None:
    """Each item waits for the other: run one at a time, the barrier would break."""
    together = threading.Barrier(2, timeout=10)

    def entry(item: int) -> int:
        together.wait()
        return item

    manifest = {'entry': 'm:f', 'items': [0, 1], 'shards': [[0, 2]], 'lanes': 2}
    assert run_shard(manifest, 0, tmp_path / RESULTS, entry) == 2
    lines = (tmp_path / RESULTS).read_text(encoding='utf-8').splitlines()
    assert sorted(json.loads(line)['data']['value'] for line in lines) == [0, 1]
