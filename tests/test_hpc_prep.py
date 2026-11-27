"""The PREP JOB (:mod:`lab_commons.hpc.prep`): nothing of a run is built or collected on the login node.

User ruling 2026-11-27: fetch, env build, native build, needs probe and collection run as their own Slurm job; the
test array follows on a later advance. These tests read what the LOGIN runner was sent.
"""

from __future__ import annotations

import json

import pytest
from test_hpc_run import COST, MACHINE, POLICY, SHA, FakeRunCluster, _gather

from lab_commons.hpc.builds import Builds
from lab_commons.hpc.needsets import BASE, Vocabulary
from lab_commons.hpc.plan import Plan
from lab_commons.hpc.prep import env_ready, prep_dir, prep_needed, prep_script, read_prep
from lab_commons.hpc.run import submit_run
from lab_commons.hpc.shell import RunSpec, build_script, collect_command

#: What only a build or a collection runs; the login node must never be sent one.
_BUILDING = (
    'lab_ci_entry_item.py ',
    'uv venv',
    'uv sync',
    'uv cache',
    'python -m pytest',
    'lab_ci_collect.py --cache',
    'cargo',
    'worktree add',
)
_FETCHING = ('git -C', 'git init', 'index-pack')


def _spec() -> RunSpec:
    builds = Builds(install='uv sync --frozen', env_key='k1', native_key='n1', native_build='cargo build')
    return RunSpec(sha=SHA, repo_url='https://g/r.git', builds=builds, vocabulary=Vocabulary(('win',)))


def _login_text(fake: FakeRunCluster) -> list[str]:
    """Every command the login runner ran; a shipped file's TEXT (stdin) is data, not a command."""
    return [command for command, _ in fake.calls]


def test_submit_sends_the_login_node_only_writes_and_one_sbatch() -> None:
    fake = FakeRunCluster()
    state = submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    sent = _login_text(fake)
    assert not [c for c in sent if any(word in c for word in (*_BUILDING, *_FETCHING))], 'no build, fetch or collect'
    assert [c for c in sent if 'sbatch ' in c] == [f'cd {prep_dir(SHA, BASE)} && sbatch --parsable prep.sbatch']
    assert state['prep_job']['job_id'] == '4242'
    assert state['rounds'] == [], 'no test array before the collection exists'
    assert not fake.manifests


def test_planted_control_the_old_login_flow_is_what_the_detector_refuses() -> None:
    """PLANTED: the scripts the login node used to run are exactly what the assertion above would catch."""
    spec = _spec()
    for script in (build_script(spec), collect_command(spec)):
        assert any(word in script for word in _BUILDING), 'the detector can say no'


def test_the_prep_script_fetches_builds_and_collects_per_resource_on_the_compute_node() -> None:
    fake = FakeRunCluster()
    submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    script = fake.prep
    assert script.startswith('#!/bin/bash\n#SBATCH --job-name=run-')
    for word in ('@@@ missing', 'cat-file', 'uv venv', 'uv sync --frozen', 'CARGO_TARGET_DIR', 'lab_ci_collect.py'):
        assert word in script
    assert '(win)' in script, 'one collection per declared resource marker'
    order = [script.index(f'"$P/{step}.sh"') for step in ('needs', 'fetch', 'built', 'collected', 'also-0')]
    assert order == sorted(order), 'probe, fetch, build, collect -- in that order'
    assert '< /dev/null' in script, 'no step reads the script it runs from'


def test_the_test_array_is_submitted_by_the_first_gather_after_the_prep_left_slurm() -> None:
    fake = FakeRunCluster()
    submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    fake.queue_prep = 'RUNNING'
    assert _gather(fake) is None
    assert not fake.manifests, 'the prep still runs: no array'
    fake.queue_prep = ''
    assert _gather(fake) is None
    assert len(fake.manifests) == 1, 'round 0 planned from the collection'
    state = json.loads(fake.state)
    assert state['env'] == fake.env
    assert state['handed'] == {'tests/w.py::t': 'win'}, 'the per-resource collection hands its ids on'


def test_a_failed_prep_build_is_not_submitted_and_quotes_its_log() -> None:
    fake = FakeRunCluster(build_fails=True)
    submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    with pytest.raises(RuntimeError, match=r'(?s)build for a{12} failed .*prep job 4242.*could not find `cargo`'):
        _gather(fake)
    assert fake.steps == ['needs', 'fetch', 'built'], 'the job stopped at the build'
    assert not fake.manifests


def test_read_prep_splits_one_read_into_its_steps() -> None:
    said = read_prep(lambda _c, _s: '@@@ prep-file needs\n@@@ prep-file fetch\nx\nhave\n', SHA, BASE)
    assert said == {'needs': '\n', 'fetch': 'x\nhave\n'}


def test_env_ready_and_prep_needed_read_the_keyed_stamps() -> None:
    stamped: set[str] = set()

    def run(command: str, _stdin: str | None) -> str:
        return 'yes\n' if any(f'/{key}/.lab-ci-installed' in command for key in stamped) else ''

    builds = _spec().builds
    assert not env_ready(run, 'k1')
    assert prep_needed(run, builds)
    stamped.add('k1')
    assert env_ready(run, 'k1')
    assert prep_needed(run, builds), 'the native build is not stamped yet'
    stamped.add('n1')
    assert not prep_needed(run, builds)


def test_the_prep_asks_the_allocated_plans_partition_and_account() -> None:
    plan = Plan(
        partitions=('shortq',), qos='q', account='acct', cpus=2, mem_mb=4096, gpus=0, minutes=60,
        shards=((0, 1),), throttle=1, free_slots=1, makespan_minutes=60, setup=('module load git',),
    )  # fmt: skip
    script = prep_script(_spec(), plan, name='run-x')
    for directive in ('--partition=shortq', '--account=acct', '--qos=q', '--mem=4096M', '--time=60'):
        assert f'#SBATCH {directive}\n' in script
    assert 'module load git\n' in script
