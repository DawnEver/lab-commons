"""The PREP JOB (:mod:`lab_commons.hpc.prep`): nothing of a run is built or collected on the login node.

User ruling 2026-11-27: fetch, env build, native build, needs probe and collection run as their own Slurm job; the
test array follows on a later advance. These tests read what the LOGIN runner was sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_hpc_run import COST, MACHINE, POLICY, SHA, FakeRunCluster, _gather

from lab_commons.hpc.builds import Builds
from lab_commons.hpc.needsets import BASE, Vocabulary
from lab_commons.hpc.plan import Plan
from lab_commons.hpc.prep import env_ready, prep_dir, prep_needed, prep_script, read_prep, submit_env_prep
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


class _Login:
    """The account's login shell as the cron tick sees it: Slurm's queue and accounting, and the job files."""

    def __init__(
        self, *, queued: str = '', last: str = '', state: str = '', log: str | None = None, kit: str = 'kit1\n'
    ) -> None:
        self.queued, self.last, self.state, self.log, self.kit = queued, last, state, log, kit
        self.probe = FakeRunCluster()
        self.calls: list[tuple[str, str | None]] = []
        self.script = ''

    def __call__(self, command: str, stdin: str | None = None) -> str:  # noqa: PLR0911 -- one answer per verb
        self.calls.append((command, stdin))
        if command.startswith('squeue -h --me -n '):
            return self.queued
        if command.endswith('/job 2>/dev/null || true'):
            return self.last
        if command.endswith('/kit 2>/dev/null || true'):
            return self.kit
        if command.startswith('sacct '):
            return self.state
        if command.startswith('[ -f ') and 'tail -n' in command:
            return '' if self.log is None else f'{self.log}@@@ log\n'
        if command.endswith('prep.sbatch') and 'cat >' in command:
            self.script = stdin or ''
            return ''
        if 'sbatch --parsable' in command:
            return '777\n'
        if 'cat >' in command:
            return ''
        return self.probe(command, stdin)


def _env_prep(login: _Login, key: str = 'k' * 40, *, kit: str = 'kit1', retry: bool = False) -> str:
    return submit_env_prep(
        Path('/acct/repos/r'),
        key,
        python='python3',
        root='/acct',
        script='cd /kit && py -m x prep-build r',
        kit=kit,
        retry=retry,
        machine=MACHINE,
        run=login,
    )


def test_env_prep_submits_one_job_keyed_by_its_key() -> None:
    login = _Login()
    assert _env_prep(login) == 'submitted job 777'
    assert '#SBATCH --job-name=envprep-kkkkkkkkkkkk' in login.script
    assert '#SBATCH --ntasks=1' in login.script
    assert '#SBATCH --account=acct-free' in login.script
    assert '#SBATCH --output=/acct/jobs/envprep/kkkkkkkkkkkk/prep.log' in login.script
    assert login.script.rstrip().endswith('cd /kit && py -m x prep-build r')
    assert [c for c, _ in login.calls if 'sbatch ' in c] == [
        'cd /acct/jobs/envprep/kkkkkkkkkkkk && sbatch --parsable prep.sbatch'
    ]


def test_env_prep_sends_the_login_node_no_build_or_fetch() -> None:
    login = _Login()
    _env_prep(login)
    sent = [c for c, _ in login.calls]
    assert not [c for c in sent if any(word in c for word in (*_BUILDING, *_FETCHING))]
    assert not [c for c in sent if 'prep-build' in c], 'the script is shipped as text, never run on the login'


def test_planted_control_a_queued_key_is_never_submitted_twice() -> None:
    login = _Login(queued='555\n')
    assert _env_prep(login) == f'job 555 already queued for {"k" * 40}'
    assert not [c for c, _ in login.calls if 'sbatch' in c]


def test_a_failed_job_is_reported_with_its_log_tail_and_not_resubmitted() -> None:
    login = _Login(last='600\n', state='FAILED\n', log='uv: lock mismatch\nexit 2\n')
    said = _env_prep(login)
    assert said == 'job 600 failed: /acct/jobs/envprep/kkkkkkkkkkkk/prep.log: FAILED: uv: lock mismatch / exit 2'
    assert not [c for c, _ in login.calls if 'sbatch' in c]


def test_a_failure_under_an_older_kit_is_resubmitted_once_naming_why() -> None:
    """Ada 2026-11-28: envprep jobs failed before the kit installed cargo, and nothing could ever retry them.

    PLANTED CONTROL: the same failure under the asking kit stays reported (the test above); here the kit moved.
    """
    login = _Login(last='600\n', state='FAILED\n', log='no cargo\n', kit='kit0\n')
    assert _env_prep(login) == 'job 600 failed under kit kit0, not kit1: submitted job 777'
    assert ('cat > /acct/jobs/envprep/kkkkkkkkkkkk/kit', 'kit1\n') in login.calls, 'the new job records its kit'
    login.kit, login.last = 'kit1\n', '777\n'
    assert _env_prep(login).startswith('job 777 failed: '), 'once: a failure under its own kit is reported'
    unrecorded = _Login(last='600\n', state='FAILED\n', log='no cargo\n', kit='')
    assert _env_prep(unrecorded) == 'job 600 failed under kit (unrecorded), not kit1: submitted job 777'


def test_the_retry_door_resubmits_over_a_failure_under_the_same_kit() -> None:
    login = _Login(last='600\n', state='FAILED\n', log='flaky\n')
    assert _env_prep(login, retry=True) == 'job 600 failed; retry asked: submitted job 777'
    assert _env_prep(_Login(), retry=True) == 'submitted job 777', 'nothing failed: a plain submit'


def test_a_failed_job_whose_log_was_removed_or_a_completed_one_is_resubmitted() -> None:
    assert _env_prep(_Login(last='600\n', state='FAILED\n')) == 'submitted job 777'
    assert _env_prep(_Login(last='600\n', state='COMPLETED\n', log='ok\n')) == 'submitted job 777'


def test_env_prep_refuses_a_broken_invocation() -> None:
    with pytest.raises(ValueError, match='needs a key and a script'):
        _env_prep(_Login(), key=' ')
    with pytest.raises(ValueError, match='needs the kit'):
        _env_prep(_Login(), kit=' ')
