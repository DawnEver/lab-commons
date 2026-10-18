"""One batch over every grant that speeds it up -- the split, the record, and a dead grant's slice re-placed.

Each grant is a fake login node answering the recorded cluster (``_hpc_fixtures``), so a plan is sized by
what Slurm actually printed; nothing reaches a cluster.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab_commons.hpc import __main__ as cli
from lab_commons.hpc import jobs
from lab_commons.hpc.cluster import Unreachable
from lab_commons.hpc.config import Config, Cost, JobSpec, Policy
from lab_commons.hpc.grants import Grant, Machine, Session
from lab_commons.hpc.jobs import Gathered, built_config, gather_job, retry_job, status_job, submit_job
from lab_commons.hpc.plan import FILES_PER_RUN, plan_on
from lab_commons.hpc.slurm import parse_inodes, parse_snapshot
from lab_commons.hpc.span import span, split
from lab_commons.outcomes.record import Event

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n@@@ shared\n'
NEAR = Grant(user='me', hosts=('near',), slurm_account='acct-free', partitions=('shortq', 'defq'))
FAR = Grant(user='you', hosts=('far',), slurm_account='acct-free', partitions=('shortq', 'defq'))
BIG = Cost(cpus=1, mem_gb=2, seconds=600)


def _snap(files: str = '') -> object:
    return parse_snapshot(IDLE + (f'@@@ files\n{files}\n' if files else ''), 'acct-free')


def test_split_cuts_consecutive_ranges_by_share_of_the_seconds() -> None:
    assert split([1.0] * 10, [1.0, 1.0]) == [(0, 5), (5, 10)]
    assert split([1.0] * 10, [3.0, 1.0]) == [(0, 8), (8, 10)] or split([1.0] * 10, [3.0, 1.0]) == [(0, 7), (7, 10)]
    assert split([9.0, 1.0, 1.0, 1.0], [1.0, 1.0]) == [(0, 1), (1, 4)], 'seconds, not item counts, are shared'


def test_a_large_batch_spans_both_grants_and_finishes_sooner() -> None:
    candidates = [(NEAR, _snap()), (FAR, _snap())]
    placements = span(2000, BIG, candidates, session=None, policy=Policy())
    assert [p.grant for p in placements] == [NEAR, FAR]
    assert placements[0].items[0] == 0
    assert placements[0].items[1] == placements[1].items[0]
    assert placements[1].items[1] == 2000
    alone = plan_on(NEAR, _snap(), 2000, BIG, session=None, policy=Policy())
    assert max(p.plan.makespan_minutes for p in placements) < alone.makespan_minutes


def test_a_small_batch_stays_on_the_one_fastest_grant() -> None:
    placements = span(4, Cost(seconds=60), [(NEAR, _snap()), (FAR, _snap())], session=None, policy=Policy())
    assert len(placements) == 1
    assert placements[0].items == (0, 4)


def test_a_spread_never_runs_more_at_once_than_the_session_cap() -> None:
    """The cap counts the whole pool: two slices of a capped session would each take the cap, so it stays one."""
    capped = Session(name='s', tasks=4, cpus=1000, mem_gb=10000)
    placements = span(2000, BIG, [(NEAR, _snap()), (FAR, _snap())], session=capped, policy=Policy())
    assert len(placements) == 1
    assert placements[0].plan.throttle <= 4
    # Planted control: a cap wider than both slices together keeps the spread.
    wide = Session(name='s', tasks=10000, cpus=10000, mem_gb=100000)
    assert len(span(2000, BIG, [(NEAR, _snap()), (FAR, _snap())], session=wide, policy=Policy())) == 2


def test_the_file_quota_is_a_resource_a_grant_is_refused_on() -> None:
    assert parse_inodes('/dev/x 2000000 1999997 3 100% /home') == 3
    assert parse_inodes('') is None
    full = _snap('/dev/x 2000000 1999997 3 100% /home')
    with pytest.raises(ValueError, match='free inodes'):
        plan_on(NEAR, full, 200, Cost(seconds=60), session=None, policy=Policy())
    placements = span(2000, BIG, [(NEAR, full), (FAR, _snap())], session=None, policy=Policy())
    assert [p.grant for p in placements] == [FAR]
    assert FILES_PER_RUN == 4, 'a run directory, its manifest, its script and its ONE results file, at any width'


class _Login:
    """One grant's login node: answers the probe, ``sbatch``, ``sacct``, the results read and the file count."""

    def __init__(self, name: str, *, up: bool = True) -> None:
        self.name, self.up, self.jobs, self.results, self.files = name, up, 0, '', '3'
        self.stdin: dict[str, str] = {}

    def __call__(self, command: str, stdin: str | None = None) -> str:
        if not self.up:
            msg = f'ssh could not reach {self.name}'
            raise Unreachable(msg)
        if stdin is not None:
            self.stdin[command] = stdin
        if 'scontrol show node' in command:
            return IDLE
        if 'sbatch' in command:
            self.jobs += 1
            return f'{self.jobs}{len(self.name)}\n'
        if 'results.jsonl' in command:
            return self.results + EARLIER
        if 'find ' in command:
            return self.files
        return ''


_OK = {'code': 0, 'tail': []}

#: What the login node prints between the results file and an earlier worker's per-shard files.
EARLIER = chr(10) + '@@@ earlier-layout' + chr(10)


def _lines(*events: tuple[str, str, dict]) -> str:
    """Results-file lines in the worker's own format (one :class:`~lab_commons.outcomes.record.Event` each)."""
    return ''.join(json.dumps(Event(kind, key, data=data).to_plain()) + chr(10) for kind, key, data in events)


def _reach(logins: dict[str, _Login]) -> object:
    return lambda grant: logins[grant.hosts[0]]


def _config(tmp_path: Path, cost: Cost = BIG) -> Config:
    return Config(job=JobSpec(name='sweep', workdir='~/p', entry='m:f'), cost=cost, source=tmp_path / 'job.toml')


def _cluster(*, far_up: bool = True) -> tuple[Machine, dict[str, _Login]]:
    logins = {'near': _Login('near'), 'far': _Login('far', up=far_up)}
    return Machine(session=None, grants=(NEAR, FAR)), logins


def test_one_record_lists_every_grants_slice_and_each_tree_is_ensured(tmp_path: Path) -> None:
    machine, logins = _cluster()
    ensured = []
    record = submit_job(
        _config(tmp_path),
        list(range(2000)),
        machine,
        lambda g: logins[g.hosts[0]],
        stamp='t0',
        ensure=ensured.append,
    )
    assert ensured == [NEAR, FAR]
    assert [p['grant'] for p in record['placements']] == [NEAR.name, FAR.name]
    near, far = record['placements']
    assert near['items'][1] == far['items'][0]
    assert far['items'][1] == 2000
    assert near['run_dir'] != far['run_dir'], 'each slice has its own run directory'
    manifest = json.loads(next(v for k, v in logins['far'].stdin.items() if 'manifest' in k))
    assert manifest['items'][0] == far['items'][0], 'a grant holds only its slice'


def test_a_grant_whose_tree_cannot_be_built_is_dropped_and_the_rest_take_the_batch(tmp_path: Path) -> None:
    machine, logins = _cluster()

    def ensure(grant: Grant) -> None:
        if grant == FAR:
            msg = 'the build failed'
            raise RuntimeError(msg)

    record = submit_job(
        _config(tmp_path), list(range(2000)), machine, lambda g: logins[g.hosts[0]], stamp='t0', ensure=ensure
    )
    assert [(p['grant'], p['items']) for p in record['placements']] == [(NEAR.name, [0, 2000])]


def test_gather_merges_the_slices_back_into_batch_order_and_names_a_silent_grant(tmp_path: Path) -> None:
    machine, logins = _cluster()
    reach = _reach(logins)
    record = submit_job(_config(tmp_path), list(range(2000)), machine, reach, stamp='t0')
    cut = record['placements'][1]['items'][0]
    logins['near'].results = _lines(('item', '0', {'shard': 0, 'ok': True, 'value': 'n'}), ('shard', '0', _OK))
    logins['far'].results = _lines(('item', '0', {'shard': 0, 'ok': True, 'value': 'f'}), ('shard', '0', _OK))
    got = gather_job(record, machine, reach, budget=10)
    assert [(r['index'], r['value']) for r in got.results] == [(0, 'n'), (cut, 'f')]
    assert (got.returned, got.silent, got.over) == (2, [], [])
    logins['far'].up = False
    got = gather_job(record, machine, reach, budget=10)
    assert (len(got.results), got.silent) == (1, [FAR.name])
    assert status_job(record, machine, reach)[1] == f'{FAR.name}: did not answer'


def test_a_dead_grants_slice_is_re_placed_on_the_grants_still_answering(tmp_path: Path) -> None:
    machine, logins = _cluster()
    reach = _reach(logins)
    items = list(range(2000))
    record = submit_job(_config(tmp_path), items, machine, reach, stamp='t0')
    dead = record['placements'][1]
    logins['far'].up = False
    logins['near'].stdin.clear()
    said = retry_job(_config(tmp_path), items, record, machine, reach)
    assert record['moved'] == [dead]
    assert [p['grant'] for p in record['placements']] == [NEAR.name, NEAR.name]
    assert record['placements'][1]['items'] == dead['items']
    assert any('did not answer' in line for line in said)
    manifest = json.loads(next(v for k, v in logins['near'].stdin.items() if 'manifest' in k))
    assert manifest['items'] == items[dead['items'][0] : dead['items'][1]]


def test_a_commit_runs_from_its_artifact_never_a_tree() -> None:
    config = Config(job=JobSpec(entry='m:f', sha='a' * 40, repo_url='https://x/r.git', setup=('echo',)))
    job = built_config(config).job
    assert job.workdir == '~', 'no checkout: the task enters the unpacked artifact'
    assert job.setup[-1] == 'echo'
    assert any(f'ci/artifacts/{"a" * 40}.tar.gz' in line for line in job.setup)
    assert not any('ci/trees' in line for line in job.setup)
    with pytest.raises(ValueError, match='come together'):
        JobSpec(sha='a' * 40)
    with pytest.raises(ValueError, match='drop \\[job\\] workdir'):
        built_config(Config(job=JobSpec(workdir='~/p', sha='a' * 40, repo_url='https://x/r.git')))
    with pytest.raises(ValueError, match='test run tree, retired for jobs'):
        built_config(Config(job=JobSpec(workdir=f'~/ci/trees/{"a" * 40}')))


def test_a_one_grant_record_is_refused_by_its_retired_name(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / 'job.submission.json').write_text(json.dumps({'grant': 'g', 'job_ids': ['1']}), encoding='utf-8')
    with pytest.raises(ValueError, match='placements'):
        cli._record(config)


# -- gather -c --wait: poll the shards' status, print each change once, gather when none is left active ----


RECORD = {
    'placements': [
        {
            'grant': NEAR.name,
            'account': list(NEAR.account),
            'targets': ['me@near'],
            'job_ids': ['1'],
            'shards': 2,
            'items': [0, 2],
            'run_dir': '~/r',
        }
    ]
}


def _wait_cli(monkeypatch: pytest.MonkeyPatch, polls: list[object]) -> tuple[list[str], list[float], list[int]]:
    """``main`` over scripted polls: each a ``{shard: state}`` answer or an :class:`Unreachable` to raise."""
    out: list[str] = []
    slept: list[float] = []
    gathered: list[int] = []
    script = iter(polls)

    def states(_run: object, _ids: object) -> dict[int, str]:
        answer = next(script)
        if isinstance(answer, Unreachable):
            raise answer
        return answer

    def gather(*_a: object, **_k: object) -> Gathered:
        gathered.append(1)
        return Gathered([], 2, [], [], [])

    monkeypatch.setattr(cli, 'load_grants', lambda: Machine(session=None, grants=(NEAR,)))
    monkeypatch.setattr(cli, 'runner_for', lambda _g, *_a: None)
    monkeypatch.setattr(cli, '_job_config', lambda _p, _a: Config(policy=Policy(poll_seconds=7)))
    monkeypatch.setattr(cli, '_record', lambda _c: RECORD)
    monkeypatch.setattr(cli, '_out', out.append)
    monkeypatch.setattr(cli, '_sleep', slept.append)
    monkeypatch.setattr(cli, 'gather_job', gather)
    monkeypatch.setattr(jobs, 'shard_states', states)
    return out, slept, gathered


def test_gather_wait_prints_each_status_change_once_and_gathers_once_nothing_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    running = {0: 'RUNNING', 1: 'PENDING'}
    polls = [
        Unreachable('no login host answered'),
        Unreachable('no login host answered'),
        running,
        running,
        {0: 'COMPLETED', 1: 'RUNNING'},
        {0: 'COMPLETED', 1: 'FAILED'},
    ]
    out, slept, gathered = _wait_cli(monkeypatch, polls)
    assert cli.main(['gather', '-c', 'j.toml', '-o', str(tmp_path / 'o.json'), '--wait']) == 0
    assert out[:4] == [
        f'pending: {NEAR.name}: no login host answered',
        f'{NEAR.name}: PENDING=1, RUNNING=1',
        f'{NEAR.name}: COMPLETED=1, RUNNING=1',
        f'{NEAR.name}: COMPLETED=1, FAILED=1',
    ]
    assert out[4].startswith('2/2 shards returned')
    assert gathered == [1], 'one gather, after the last shard left RUNNING'
    assert slept == [7] * 5


def test_gather_without_wait_gathers_at_once_and_wait_belongs_to_gather(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, slept, gathered = _wait_cli(monkeypatch, [])
    assert cli.main(['gather', '-c', 'j.toml', '-o', str(tmp_path / 'o.json')]) == 0
    assert (gathered, slept) == ([1], [])
    assert out[0].startswith('2/2 shards returned'), 'no status poll without --wait'
    for verb in ['plan', 'submit', 'status', 'retry']:
        with pytest.raises(SystemExit):
            cli.main([verb, '-c', 'j.toml', '--wait'])
        assert '--wait belongs to gather' in capsys.readouterr().err
