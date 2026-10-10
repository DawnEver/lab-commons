"""``lab_commons.hpc`` -- parsing, planning, submission and the worker, against real output.

The fixture is ``scontrol``/``sacctmgr``/``squeue`` text captured on 2026-10-08 through the module's own
:data:`~lab_commons.hpc.slurm.PROBE_COMMAND`, host fields stripped and node, partition, QOS and account
names anonymized by one consistent mapping. Every rule is asserted against what the cluster actually
printed, so a parser that drifts from Slurm's real format reds here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lab_commons.hpc.cluster import _completed, gather, render_script, shard_states, submit
from lab_commons.hpc.config import Cluster, Config, Cost, JobSpec, Limits, Policy, load_config
from lab_commons.hpc.plan import free_slots, make_plan, quota_slots
from lab_commons.hpc.slurm import parse_minutes, parse_records, parse_snapshot, parse_tres
from lab_commons.hpc.worker import main as worker_main
from lab_commons.hpc.worker import run_shard
from lab_commons.width import fits

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
#: The same instant with none of the caller's own jobs running -- the quota at its full width.
IDLE_CALLER = FIXTURE.split('@@@ running')[0] + '@@@ running\n'

CLUSTER = Cluster(partitions=('devq', 'shortq', 'defq', 'hmemq'), qos={'devq': 'dev'})


def test_a_value_with_spaces_stays_whole() -> None:
    record = parse_records('NodeName=n1 OS=Linux 4.18 #1 SMP State=MIXED')[0]
    assert record == {'NodeName': 'n1', 'OS': 'Linux 4.18 #1 SMP', 'State': 'MIXED'}


@pytest.mark.parametrize(
    ('text', 'minutes'),
    [('7-00:00:00', 7 * 24 * 60), ('01:00:00', 60), ('12:30', 12.5), ('90', 90), ('UNLIMITED', None), ('', None)],
)
def test_durations(text: str, minutes: float | None) -> None:
    assert parse_minutes(text) == minutes


def test_tres_keeps_the_untyped_gpu_and_megabytes() -> None:
    tres = parse_tres('cpu=96,gres/gpu:a100-full=1,gres/gpu=2,mem=360G,node=1')
    assert tres == {'cpu': 96, 'gpu': 2, 'mem': 360 * 1024}


def test_the_recorded_cluster_parses() -> None:
    snap = parse_snapshot(FIXTURE)
    assert len(snap.nodes) == 136
    assert snap.partitions['defq'].max_minutes == 7 * 24 * 60
    assert snap.quota.account == 'acct-free'
    assert snap.quota.default_qos == 'free'
    assert snap.quota.qos_minutes['free'] == 2 * 24 * 60


def test_free_is_configured_minus_allocated() -> None:
    node = next(n for n in parse_snapshot(FIXTURE).nodes if n.name == 'gpu001')
    assert node.cpus == 96 - 59
    assert node.mem_mb == 773700 - 8192 - 387072
    assert node.gpus == 0
    assert node.schedulable


def test_running_jobs_are_taken_off_the_quota() -> None:
    held = parse_snapshot(FIXTURE).quota
    assert held.cpus == 96
    assert held.remaining()['cpu'] == 0
    full = parse_snapshot(IDLE_CALLER).quota
    assert full.cpus == 96
    assert full.mem_mb == 360 * 1024
    assert full.gpus == 2


def test_a_missing_section_is_an_error_not_an_empty_cluster() -> None:
    with pytest.raises(ValueError, match='nodes'):
        parse_snapshot('@@@ partitions\n@@@ assoc\n')


def test_local_limits_only_lower_the_quota() -> None:
    snap = parse_snapshot(IDLE_CALLER)
    cost = Cost(cpus=4, mem_gb=8)
    assert quota_slots(snap, cost, Limits()) == 24
    assert quota_slots(snap, cost, Limits(cpus=40)) == 10
    assert quota_slots(snap, cost, Limits(cpus=10_000)) == 24


def test_the_plan_fits_the_shard_window_and_the_quota() -> None:
    snap = parse_snapshot(IDLE_CALLER)
    plan = make_plan(500, Cost(cpus=1, mem_gb=2, seconds=60), snap, cluster=CLUSTER, policy=Policy(), limits=Limits())
    per_shard = plan.shards[0][1] - plan.shards[0][0]
    assert per_shard == 5  # free QOS takes 100 jobs per user: 500 items need 5 a shard (window alone: 4)
    assert plan.shards[-1][1] == 500
    assert plan.minutes == 8
    assert plan.partitions[0] == 'shortq'
    assert plan.qos == 'free'
    assert plan.throttle == 96
    assert plan.free_slots == free_slots(snap, plan.partitions, Cost(cpus=1, mem_gb=2, seconds=60))


def test_a_long_shard_skips_partitions_whose_ceiling_it_breaks() -> None:
    snap = parse_snapshot(IDLE_CALLER)
    plan = make_plan(10, Cost(seconds=3 * 3600), snap, cluster=CLUSTER, policy=Policy(), limits=Limits())
    assert plan.partitions[0] == 'shortq'  # devq's 1 h ceiling cannot hold a 4.5 h shard
    plan = make_plan(10, Cost(seconds=20 * 3600), snap, cluster=CLUSTER, policy=Policy(), limits=Limits())
    assert plan.partitions[0] == 'defq'
    with pytest.raises(ValueError, match='no candidate partition can take'):
        make_plan(
            10, Cost(seconds=40 * 3600), snap, cluster=CLUSTER, policy=Policy(), limits=Limits()
        )  # beyond the 2-day free QOS


def test_a_held_quota_throttles_by_the_ceiling_and_only_delays() -> None:
    plan = make_plan(10, Cost(), parse_snapshot(FIXTURE), cluster=CLUSTER, policy=Policy(), limits=Limits())
    assert plan.throttle == len(plan.shards)  # not 0: Slurm pends them until the caller's own job ends


def test_an_item_wider_than_the_ceiling_is_refused() -> None:
    with pytest.raises(ValueError, match='quota'):
        make_plan(10, Cost(cpus=200), parse_snapshot(IDLE_CALLER), cluster=CLUSTER, policy=Policy(), limits=Limits())


def test_the_array_never_outgrows_max_array() -> None:
    plan = make_plan(
        10_000,
        Cost(seconds=1),
        parse_snapshot(IDLE_CALLER),
        cluster=CLUSTER,
        policy=Policy(max_array=100),
        limits=Limits(),
    )
    assert len(plan.shards) <= 100


class FakeCluster:
    """A runner that records commands and answers the few a run issues."""

    def __init__(self, answers: dict[str, str]) -> None:
        """*answers* maps a substring of a command to the stdout it gets."""
        self.answers = answers
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, command: str, stdin: str | None = None) -> str:
        self.calls.append((command, stdin))
        return next((out for key, out in self.answers.items() if key in command), '')


def _config(tmp_path: Path) -> Config:
    return Config(
        job=JobSpec(name='sweep', workdir='~/proj', setup=('source .venv/bin/activate',), entry='m:f'),
        source=tmp_path / 'hpc.toml',
    )


def test_submit_writes_the_manifest_and_the_script_then_submits(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = make_plan(
        10, Cost(seconds=60), parse_snapshot(IDLE_CALLER), cluster=CLUSTER, policy=Policy(), limits=Limits()
    )
    fake = FakeCluster({'sbatch': '7654321\n'})
    sub = submit(fake, plan, config, list(range(10)), stamp='t0')
    assert sub.job_id == '7654321'
    assert sub.run_dir == '~/proj/.lab-hpc/sweep-t0'
    manifest = json.loads(fake.calls[0][1] or '')
    assert manifest['shards'] == [list(s) for s in plan.shards]
    assert '--qos=dev' in (fake.calls[1][1] or '')
    assert f'--array=0-{len(plan.shards) - 1}%{plan.throttle}' in fake.calls[2][0]


def test_a_resubmit_reuses_the_run_and_names_only_its_shards(tmp_path: Path) -> None:
    plan = make_plan(
        10, Cost(seconds=60), parse_snapshot(IDLE_CALLER), cluster=CLUSTER, policy=Policy(), limits=Limits()
    )
    fake = FakeCluster({'sbatch': '99\n'})
    submit(fake, plan, _config(tmp_path), list(range(10)), stamp='t0', only=[2, 0])
    assert len(fake.calls) == 1
    assert '--array=0,2%' in fake.calls[0][0]


def test_the_script_keeps_home_expandable(tmp_path: Path) -> None:
    plan = make_plan(
        10, Cost(seconds=60), parse_snapshot(IDLE_CALLER), cluster=CLUSTER, policy=Policy(), limits=Limits()
    )
    script = render_script(plan, _config(tmp_path), '~/proj/.lab-hpc/sweep-t0')
    assert 'cd "$HOME"/proj' in script
    assert 'source .venv/bin/activate' in script
    assert script.startswith('#!/bin/bash\n#SBATCH --job-name=sweep')


def test_states_expand_pending_ranges_and_a_resubmit_supersedes() -> None:
    fake = FakeCluster({'-j 1 ': '1_0|COMPLETED\n1_1|FAILED\n1_[2-3%4]|PENDING\n', '-j 2 ': '2_1|RUNNING\n'})
    assert shard_states(fake, ['1', '2']) == {0: 'COMPLETED', 1: 'RUNNING', 2: 'PENDING', 3: 'PENDING'}


def test_gather_reads_one_line_per_shard() -> None:
    line = json.dumps({'shard': 3, 'results': [{'index': 9, 'ok': True, 'value': 1}]})
    assert gather(FakeCluster({'results': line + '\n\n'}), '~/r') == {3: [{'index': 9, 'ok': True, 'value': 1}]}


def test_an_item_failure_is_recorded_and_the_shard_continues() -> None:
    manifest = {'entry': 'unused:unused', 'items': [1, 0, 2], 'shards': [[0, 3]]}
    record = run_shard(manifest, 0, entry=lambda x: 6 // x)
    assert [r['ok'] for r in record['results']] == [True, False, True]
    assert 'ZeroDivisionError' in record['results'][1]['error']


def test_the_worker_writes_its_shard_file(tmp_path: Path) -> None:
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'entry': 'math:sqrt', 'items': [4, 9, -1], 'shards': [[0, 2], [2, 3]]}))
    assert worker_main([str(manifest), '1']) == 0
    record = json.loads((tmp_path / 'results' / '1.json').read_text(encoding='utf-8'))
    assert record['shard'] == 1
    assert record['results'][0]['ok'] is False


def test_config_round_trip_and_typos_are_refused(tmp_path: Path) -> None:
    path = tmp_path / 'hpc.toml'
    path.write_text(
        '[cost]\ncpus = 2\nmem_gb = 4\n[job]\nitems = "items.json"\n[policy]\nshard_minutes_max = 30\n',
        encoding='utf-8',
    )
    config = load_config(path)
    assert config.policy.shard_minutes_max == 30
    assert config.cost == Cost(cpus=2, mem_gb=4)
    assert config.items_path() == tmp_path / 'items.json'
    path.write_text('[cost]\ncpu = 2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='unknown keys'):
        load_config(path)


@pytest.mark.parametrize('moved', ['cluster', 'limits'])
def test_where_a_job_runs_is_not_the_job_files_to_say(tmp_path: Path, moved: str) -> None:
    """Host, account, partitions and the share moved to the machine's grants file."""
    path = tmp_path / 'hpc.toml'
    path.write_text(f'[{moved}]\n', encoding='utf-8')
    with pytest.raises(ValueError, match=f"unknown sections \\['{moved}'\\]"):
        load_config(path)


def test_fits_is_the_tightest_dimension() -> None:
    assert fits({'cpu': 96, 'memory': 360 * 2**30}, {'cpu': 4, 'memory': 32 * 2**30}) == 11
    assert fits({'cpu': 96, 'memory': None}, {'cpu': 4, 'memory': 1}) == 24
    assert fits({'cpu': 96}, {'cpu': 1, 'gpu': 0}) == 96
    assert fits({'cpu': 96}, {'cpu': 1, 'gpu': 1}) == 0, 'an unread capacity is not an unlimited one'
    with pytest.raises(ValueError, match='DIMENSIONS'):
        fits({'cpu': 1}, {'cores': 1})


def test_stdin_reaches_the_child_byte_for_byte() -> None:
    """A text-mode pipe on Windows turned the script's LF into CRLF and sbatch refused it."""
    echo = [sys.executable, '-c', 'import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))']
    assert _completed(echo, 'a\nb\n') == repr(b'a\nb\n')


def test_a_small_batch_fits_the_dev_qos_submit_limit() -> None:
    snap = parse_snapshot(IDLE_CALLER)
    plan = make_plan(3, Cost(seconds=60), snap, cluster=CLUSTER, policy=Policy(), limits=Limits())
    assert plan.partitions[0] == 'devq'
    assert len(plan.shards) <= (snap.quota.qos_max_submit['dev'] or 0)


def test_queued_jobs_use_up_the_submit_room() -> None:
    snap = parse_snapshot(IDLE_CALLER + '@@@ queued\n' + 'dev\n' * 4)
    assert snap.quota.submit_room('dev') == 0
    plan = make_plan(3, Cost(seconds=60), snap, cluster=CLUSTER, policy=Policy(), limits=Limits())
    assert plan.partitions[0] == 'shortq'
