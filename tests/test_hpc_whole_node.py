"""A whole-node task: ``[task] exclusive`` and ``lane_domains`` -- one array task, one node, cut into lanes.

The lanes are derived from the grant's architecture in the user's cluster catalog (a SYNTHETIC one here, shaped
like a 96-CPU L3-as-NUMA node). Each grant is a fake login node answering the recorded cluster
(``_hpc_fixtures``); nothing reaches a cluster.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from lab_commons.hpc import jobs
from lab_commons.hpc.cluster import render_script, submit
from lab_commons.hpc.config import Config, Cost, JobSpec, Task, load_config
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.plan import Plan, Unplaceable
from lab_commons.hpc.worker import RESULTS, lane_env, run_shard

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n@@@ shared\n'
#: shortq and defq hold only 96-core nodes in the recorded cluster.
NODE96 = Grant(user='me', hosts=('near',), slurm_account='acct-free', partitions=('shortq', 'defq'), architecture='a')
ITEM = Cost(mem_gb=40, seconds=600)
#: 16 domains of 6 CPUs, two sockets, domain k = NUMA node k.
CATALOG = (
    '[a.class.standard]\npartitions = ["defq", "devq", "shortq"]\nsockets = 2\ndomains = [\n'
    + ''.join(f'  {{ cpus = "{6 * k}-{6 * k + 5}", numa = {k} }},\n' for k in range(16))
    + ']\n'
)
SHAPE = Task(exclusive=True, lane_domains=2)


@pytest.fixture(autouse=True)
def _catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(tmp_path / 'config.toml'))
    (tmp_path / 'hpc').mkdir()
    (tmp_path / 'hpc' / 'clusters.toml').write_text(CATALOG, encoding='utf-8')


def _login(command: str, stdin: str | None = None) -> str:
    del stdin
    if 'scontrol show node' in command:
        return IDLE
    if 'sbatch' in command:
        return '42\n'
    return ''


def _config(task: Task, cost: Cost = ITEM) -> Config:
    return Config(job=JobSpec(name='relay', workdir='~/p', entry='m:f'), cost=cost, task=task)


def _plan(config: Config, n_items: int, grant: Grant = NODE96) -> Plan:
    machine = Machine(session=None, grants=(grant,))
    (placement,) = jobs.plan_job(config, n_items, machine, lambda _g: _login)
    assert placement.items == (0, n_items)
    assert placement.grant == grant
    return placement.plan


def test_the_task_section_loads_a_topology_shape(tmp_path: Path) -> None:
    path = tmp_path / 'job.toml'
    path.write_text('[cost]\nmem_gb = 40\n[task]\nexclusive = true\nlane_domains = 2\n', encoding='utf-8')
    assert load_config(path).task == SHAPE
    path.write_text('[cost]\ncpus = 12\n[task]\nexclusive = true\nlane_domains = 2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='derives'):
        load_config(path)
    with pytest.raises(ValueError, match='exclusive'):
        Task(lane_domains=2)
    with pytest.raises(ValueError, match='lane_domains'):
        Task(exclusive=True, lane_domains=0)


def test_two_domain_lanes_on_the_architecture_are_twelve_cpus_by_eight() -> None:
    plan = _plan(_config(SHAPE), 20)
    assert (plan.cpus, plan.mem_mb, plan.exclusive, plan.lanes) == (96, 8 * 40 * 1024, True, 8)
    assert plan.lane_cpus[0] == '0-11'
    assert plan.lane_cpus[7] == '84-95'
    assert plan.lane_mems[7] == '14-15'
    assert plan.throttle == 1, 'acct-free holds cpu=96: one whole node at a time'
    assert plan.shards[0][0] == 0
    assert plan.shards[-1][1] == 20
    assert all(a % 8 == 0 for a, _ in plan.shards), 'a shard starts on a lane boundary'
    rounds = max(-(-(b - a) // 8) for a, b in plan.shards)
    assert plan.minutes == rounds * 15, 'each round of 8 lanes is one item: 600 s x 1.5'
    four = _plan(_config(Task(exclusive=True, lane_domains=4)), 20)
    assert (four.lanes, four.lane_cpus[1]) == (4, '24-47')
    # Planted control: one item a task is planned per item, never exclusive, never bound.
    narrow = _plan(_config(Task(), Cost(cpus=12, mem_gb=40, seconds=600)), 20)
    assert (narrow.cpus, narrow.exclusive, narrow.lanes, narrow.lane_cpus) == (12, False, 1, ())


def test_a_topology_shape_keeps_only_partitions_its_class_describes() -> None:
    mixed = replace(NODE96, partitions=('hmemq', 'shortq'))
    assert _plan(_config(SHAPE), 8, mixed).partitions == ('shortq',)


@pytest.mark.parametrize(
    ('grant', 'catalog', 'why'),
    [
        (replace(NODE96, architecture=''), CATALOG, 'names no architecture'),
        (replace(NODE96, partitions=('hmemq',)), CATALOG, 'no node class'),
        (NODE96, CATALOG.replace('"0-5"', '"0-5,96-101"'), 'Slurm reports'),
        # A defect of the catalog or the grant is not a lack of room: no costed grant may answer it.
        (replace(NODE96, architecture='b'), CATALOG, "no architecture 'b'"),
        (NODE96, None, 'junction'),
    ],
)
def test_a_shape_the_cluster_cannot_hold_is_refused(
    tmp_path: Path, grant: Grant, catalog: str | None, why: str
) -> None:
    path = tmp_path / 'hpc' / 'clusters.toml'
    if catalog is None:
        path.unlink()
    else:
        path.write_text(catalog, encoding='utf-8')
    room = why in {'names no architecture', 'no node class', 'Slurm reports'}
    with pytest.raises(Unplaceable if room else ValueError, match=why) as refused:
        _plan(_config(SHAPE), 8, grant)
    assert isinstance(refused.value, Unplaceable) == room


def test_a_lane_that_does_not_tile_the_node_is_refused() -> None:
    with pytest.raises(Unplaceable, match='lane_domains'):
        _plan(_config(Task(exclusive=True, lane_domains=3)), 8)


def test_an_exclusive_one_item_task_narrower_than_its_nodes_is_refused() -> None:
    """Slurm charges an exclusive task the whole node: a plan priced at less would overrun the quota."""
    with pytest.raises(Unplaceable, match='96-CPU node'):
        _plan(_config(Task(exclusive=True), Cost(cpus=48, mem_gb=40, seconds=600)), 8)
    whole = _plan(_config(Task(exclusive=True), Cost(cpus=96, mem_gb=40, seconds=600)), 8)
    assert (whole.cpus, whole.lanes, whole.lane_cpus) == (96, 1, ())


def test_the_script_asks_for_the_node_and_threads_each_lane_to_its_cpus() -> None:
    config = _config(SHAPE)
    script = render_script(_plan(config, 16), config, '~/p/.lab-hpc/relay-t0')
    assert '#SBATCH --exclusive\n' in script
    assert '#SBATCH --cpus-per-task=96\n' in script
    assert 'OMP_NUM_THREADS=12 ' in script
    narrow = _config(Task(), Cost(cpus=12, mem_gb=40, seconds=600))
    assert '--exclusive' not in render_script(_plan(narrow, 16), narrow, '~/p/.lab-hpc/relay-t0')


def test_the_manifest_carries_each_lanes_cpus_and_memory_nodes() -> None:
    calls: list[tuple[str, str | None]] = []

    def login(command: str, stdin: str | None = None) -> str:
        calls.append((command, stdin))
        return _login(command, stdin)

    config = _config(SHAPE)
    submit(login, _plan(config, 16), config, list(range(16)), stamp='t0')
    lanes = json.loads(calls[0][1] or '')['lanes']
    assert len(lanes) == 8
    assert lanes[1] == {'cpus': '12-23', 'mems': '2-3'}


def test_the_worker_runs_items_lanes_at_once_each_on_its_own_lane(tmp_path: Path) -> None:
    """Each item waits for the other: run one at a time, the barrier would break."""
    together = threading.Barrier(2, timeout=10)

    def entry(_item: int) -> dict[str, str]:
        together.wait()
        return lane_env()

    lanes = [{'cpus': '0-11', 'mems': '0-1'}, {'cpus': '12-23', 'mems': '2-3'}]
    manifest = {'entry': 'm:f', 'items': [0, 1], 'shards': [[0, 2]], 'lanes': lanes}
    assert run_shard(manifest, 0, tmp_path / RESULTS, entry) == 2
    values = [json.loads(line)['data']['value'] for line in (tmp_path / RESULTS).read_text().splitlines()]
    assert sorted((v['LC_HPC_LANE_CPUS'], v['LC_HPC_LANE_MEMS']) for v in values) == [
        ('0-11', '0-1'),
        ('12-23', '2-3'),
    ]
    assert lane_env() == {}, 'outside a lane there is no binding'


def test_an_unbound_shard_has_no_lane_binding(tmp_path: Path) -> None:
    manifest = {'entry': 'm:f', 'items': [0, 1], 'shards': [[0, 2]], 'lanes': []}
    assert run_shard(manifest, 0, tmp_path / RESULTS, lambda _item: lane_env()) == 2
    values = [json.loads(line)['data']['value'] for line in (tmp_path / RESULTS).read_text().splitlines()]
    assert values == [{}, {}]
