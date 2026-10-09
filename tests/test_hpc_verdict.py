"""``lab_commons.hpc`` remote verdict: streamed outcomes, retried kills and measured plans.

Every number in a docstring here was measured on Ada for the verdict of motronics 64c85da4d9
(2026-10-09, job 7655849): 26233 passed, 13751 lost, 8786 missing. The item runner is exercised by
REALLY running pytest on ids built to be awkward, because the defect behind ``missing`` was a mismatch
between the id pytest collects and the id it reports -- a fake cannot reproduce that.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lab_commons.hpc.config import Cost, Limits, Policy
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.measured import Measured
from lab_commons.hpc.plan import make_plan
from lab_commons.hpc.pytest_item import canonical, outcome_of, read_stream
from lab_commons.hpc.slurm import parse_snapshot
from lab_commons.hpc.verdict import RETRIES, VerdictSpec, parse_ids, read_streams, remote_verdict

FIXTURE = (Path(__file__).parent / '_hpc_fixtures' / 'cluster-2026-10-08.txt').read_text(encoding='utf-8')
IDLE = FIXTURE.split('@@@ running')[0] + '@@@ running\n'
GRANT = Grant(user='me', hosts=('login.example',), slurm_account='acct-free', cpus=64, partitions=('shortq', 'defq'))
SHA = 'a' * 40
SOURCE = Path(__file__).parents[1] / 'src' / 'lab_commons' / 'hpc' / 'pytest_item.py'


def test_the_item_runner_is_standalone() -> None:
    """It is shipped as text and imported beside an arbitrary lab_commons -- it must import none."""
    source = SOURCE.read_text(encoding='utf-8')
    assert 'lab_commons' not in ''.join(line for line in source.splitlines() if line.startswith(('import', 'from')))


# -- the stream ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('when', 'outcome', 'xfail', 'expected'),
    [
        ('setup', 'passed', False, None),
        ('call', 'passed', False, 'passed'),
        ('call', 'failed', False, 'failed'),
        ('setup', 'failed', False, 'error'),
        ('teardown', 'failed', False, 'error'),
        ('setup', 'skipped', False, 'skipped'),
        ('call', 'skipped', True, 'xfailed'),
    ],
)
def test_a_phase_decides_only_what_it_can(when: str, outcome: str, *, xfail: bool, expected: str | None) -> None:
    assert outcome_of(when, outcome, xfail=xfail) == expected


def test_the_xdist_group_suffix_is_stripped_only_back_to_a_collected_id() -> None:
    """``--dist loadgroup`` turned ``t`` into ``t@heavy_parallel_0``; 8786 ran tests read as missing."""
    expected = {'tests/a.py::t[x@y]', 'tests/a.py::u'}
    assert canonical('tests/a.py::u@heavy_parallel_0', expected) == 'tests/a.py::u'
    assert canonical('tests/a.py::t[x@y]', expected) == 'tests/a.py::t[x@y]', 'an @ inside the id is the id'
    assert canonical('tests/a.py::v@g', expected) == 'tests/a.py::v@g', 'nothing collected: left alone'


def test_an_unclosed_stream_is_unfinished_and_keeps_what_it_finished() -> None:
    text = (
        '{"id": "t.py::a", "outcome": "passed", "s": 1.5}\n'
        '{"id": "t.py::b", "outcome": "passed", "s": 0.5}\n'
        '{"id": "t.py::b", "outcome": "error", "s": 0.25}\n'
        '{"id": "t.py::c", "outc'
    )
    folded = read_stream(text, ['t.py::a', 't.py::b', 't.py::c'])
    assert folded['done'] is None
    assert folded['outcomes'] == {'t.py::a': 'passed', 't.py::b': 'error'}
    assert folded['seconds'] == {'t.py::a': 1.5, 't.py::b': 0.75}


def test_streams_are_read_back_per_item_in_one_command() -> None:
    calls = []

    def run(command: str, stdin: str | None) -> str:
        calls.append((command, stdin))
        return '@@@ 0.jsonl\n{"a": 1}\n@@@ 12.jsonl\n{"b": 2}\n{"c": 3}\n'

    assert read_streams(run, '"$HOME"/ci/x') == {0: '{"a": 1}', 12: '{"b": 2}\n{"c": 3}'}
    assert len(calls) == 1


# -- the item runner, for real ---------------------------------------------------------------------------

_TRICKY = """
import os
import pytest

@pytest.mark.parametrize('v', ['a[b]', 'x::y', 'p/q', 'ünï', '-neg', 'a-b', 'sp ace', 'at@sign'])
def test_param(v):
    assert v

@pytest.mark.grouped
@pytest.mark.parametrize('n', [1, -1])
def test_grouped(n):
    assert n > 0

class TestKind:
    @pytest.mark.xfail(reason='known')
    def test_xfail(self):
        raise AssertionError

    @pytest.mark.skip(reason='not here')
    def test_skip(self):
        pass

@pytest.fixture
def broken():
    raise RuntimeError('setup')

def test_setup_error(broken):
    pass
"""

#: What xdist's ``--dist loadgroup`` does to a grouped test's id inside a worker.
_LOADGROUP = """
def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker('grouped'):
            item._nodeid = item.nodeid + '@heavy_parallel_0'
"""


def _item_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The runner as the cluster has it: ``lab_ci_pytest_item.py`` on PYTHONPATH, imported by that name."""
    shipped = tmp_path / 'bin' / 'lab_ci_pytest_item.py'
    shipped.parent.mkdir()
    shipped.write_text(SOURCE.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(shipped.parent))
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location('lab_ci_pytest_item', shipped)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, body: str) -> list[str]:
    (tmp_path / 'pytest.ini').write_text('[pytest]\nmarkers =\n    grouped: x\n', encoding='utf-8')
    (tmp_path / 'conftest.py').write_text(_LOADGROUP, encoding='utf-8')
    test = tmp_path / 'tests' / 'sub dir' / 'test_tricky.py'
    test.parent.mkdir(parents=True)
    test.write_text(body, encoding='utf-8')
    collected = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        cwd=tmp_path,
    )
    return parse_ids(collected.stdout)


def test_every_awkward_id_that_ran_is_reported_under_its_collected_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brackets, '::', '/', unicode, a leading '-', a space and an '@' in params, and a loadgroup suffix."""
    runner = _item_runner(tmp_path, monkeypatch)
    ids = _tree(tmp_path, _TRICKY)
    assert len(ids) == 13
    result = runner.run({'ids': ids, 'stream': '.lab-ci/t/0.jsonl'})
    outcomes = result['outcomes']
    assert set(outcomes) == set(ids), result['tail']
    grouped = sorted(n for n in ids if 'test_grouped' in n)
    assert [outcomes[n] for n in grouped] == ['failed', 'passed']
    assert outcomes[next(n for n in ids if n.endswith('test_xfail'))] == 'xfailed'
    assert outcomes[next(n for n in ids if n.endswith('test_skip'))] == 'skipped'
    assert outcomes[next(n for n in ids if n.endswith('test_setup_error'))] == 'error'
    assert sum(o == 'passed' for o in outcomes.values()) == 9
    assert result['done']['done'] == 1
    assert result['done']['wall'] > 0


def test_a_pytest_that_dies_mid_item_closes_the_stream_and_names_only_the_rest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _item_runner(tmp_path, monkeypatch)
    body = 'import os\n\ndef test_a():\n    pass\n\ndef test_b():\n    os._exit(3)\n\ndef test_c():\n    pass\n'
    ids = _tree(tmp_path, body)
    result = runner.run({'ids': ids, 'stream': '.lab-ci/t/0.jsonl'})
    assert result['outcomes'] == {ids[0]: 'passed'}
    assert result['done']['done'] == 3, 'it closed, so b and c are missing -- re-running would crash again'


# -- the measured plan -----------------------------------------------------------------------------------


def test_shards_are_cut_by_measured_time_and_the_wall_is_the_slowest() -> None:
    """A flat 120 s per file planned 129-min shards that ran 1h25-2h07; 23 of 99 hit the limit."""
    seconds = [600.0] * 6 + [3000.0] + [60.0] * 20
    plan = make_plan(
        len(seconds),
        Cost(seconds=120),
        parse_snapshot(IDLE),
        cluster=GRANT.cluster(),
        policy=Policy(shard_minutes_min=20, shard_minutes_max=60, safety=1.5),
        limits=Limits(),
        seconds=seconds,
    )
    padded = [s * 1.5 for s in seconds]
    sums = [sum(padded[a:b]) for a, b in plan.shards]
    assert plan.minutes == max(1, -(-int(max(sums)) // 60))
    assert all(s <= 60 * 60 or b - a == 1 for s, (a, b) in zip(sums, plan.shards, strict=True))
    assert (6, 7) in plan.shards, 'the 75-min item sits alone rather than dragging neighbours over the window'


def test_measured_estimates_fall_back_only_for_files_never_measured() -> None:
    measured = Measured.of({'durations': {'a.py::x': 2.0, 'a.py::y': 4.0}, 'overheads': {'a.py': 10.0}})
    seconds, unmeasured = measured.estimate([['a.py::x', 'a.py::y', 'a.py::z'], ['b.py::t']], Cost(seconds=120))
    assert seconds == [10.0 + 2.0 + 4.0 + 3.0, 120.0]
    assert unmeasured == ['b.py']


def test_memory_is_the_worst_measured_peak_with_margin() -> None:
    """3 shards were OOM-killed at 6 GB; the completed ones peaked at 5.15 GB."""
    measured = Measured(peaks_mb={'a.py': 5274.0, 'b.py': 900.0})
    assert measured.mem_gb([['a.py::t'], ['b.py::t']], Cost(mem_gb=6), Policy(safety=1.5)) == pytest.approx(
        5274.0 * 1.5 / 1024
    )  # floor: relative 1e-6 default, the product is exact arithmetic
    assert measured.mem_gb([['a.py::t'], ['c.py::t']], Cost(mem_gb=9), Policy(safety=1.5)) == 9, 'unmeasured: cost'


# -- the verdict flow ------------------------------------------------------------------------------------


class FakeAda:
    """A cluster answering the verdict flow; an item of a file in *dies* is killed in its first *kills* rounds.

    A killed item streams the first half of its ids (rounded down) and never closes.
    """

    def __init__(
        self, *, have: bool = True, dies: frozenset[str] = frozenset(), kills: int = 1, oom: bool = False
    ) -> None:
        """*have*: whether the cache holds the commit; *oom*: round 0 reports OUT_OF_MEMORY."""
        self.have, self.dies, self.kills, self.oom = have, dies, kills, oom
        self.calls: list[tuple[str, str | None]] = []
        self.manifests: list[dict] = []
        self.scripts: list[str] = []

    def __call__(self, command: str, stdin: str | None = None) -> str:  # noqa: C901, PLR0911 -- one answer per verb
        self.calls.append((command, stdin))
        if command.startswith('echo "@@@ nodes"'):
            return IDLE + '@@@ shared\n8 lc:ws=other\n'
        if 'cat-file' in command:
            return 'have\n' if self.have else 'missing\n'
        if '@@@ facts' in command:
            return '@@@ facts\nplatform=linux-x86_64/glibc2.28\npython=3.13.1\n'
        if '--collect-only' in command:
            if '(win)' in command:
                return 'tests/w.py::t\n'
            return 'tests/a.py::t1\ntests/a.py::t2\ntests/w.py::t\ntests/b.py::t3\ntests/b.py::t4\ntests/b.py::t5\n'
        if command.endswith('manifest.json'):
            self.manifests.append(json.loads(stdin or ''))
        if command.endswith('job.sh') and 'cat >' in command:
            self.scripts.append(stdin or '')
        if 'sbatch' in command:
            return f'{4242 + len(self.manifests)}\n'
        if 'sacct' in command:
            state = 'OUT_OF_MEMORY' if self.oom and len(self.manifests) == 1 else 'TIMEOUT'
            return '\n'.join(f'1_{i}|{state if i == 0 else "COMPLETED"}' for i in range(9)) + '\n'
        if '*.jsonl' in command:
            return self._streams()
        return ''

    def _streams(self) -> str:
        out = []
        dying = len(self.manifests) <= self.kills
        for index, item in enumerate(self.manifests[-1]['items']):
            killed = dying and item['ids'][0].split('::')[0] in self.dies
            ran = item['ids'][: len(item['ids']) // 2] if killed else item['ids']
            out.append(f'@@@ {index}.jsonl')
            out += [json.dumps({'id': n, 'outcome': 'passed', 's': 1.0}) for n in ran]
            if not killed:
                out.append(json.dumps({'done': 0, 'wall': 5.0 + len(ran), 'peak_mb': 2048.0}))
        return '\n'.join(out) + '\n'


def _verdict(ada: FakeAda, **kw: object) -> dict:
    return remote_verdict(
        VerdictSpec(sha=SHA, repo_url='https://g/r.git', install='true', table={'win': ('windows',)}),
        Machine(workstation='ws-a', grants=(GRANT,)),
        lambda _g: ada,
        cost=Cost(seconds=600),
        policy=Policy(shard_minutes_min=1, shard_minutes_max=15),
        sleep=lambda _s: None,
        stamp='t0',
        **kw,
    )


def test_the_verdict_records_outcomes_and_never_passes_what_it_did_not_run() -> None:
    ada = FakeAda()
    record = _verdict(ada)
    assert record['sha'] == SHA
    assert record['left'] == {'tests/w.py::t': ['windows']}, 'left names the platforms that CAN run it'
    assert (record['platform'], record['cluster'], record['python']) == (
        'linux-x86_64/glibc2.28',
        'login.example',
        '3.13.1',
    )
    assert record['plan']['comment'] == 'lc:ws=ws-a'
    assert record['plan']['job_id'] == '4243'
    assert record['plan']['unmeasured'] == ['tests/a.py', 'tests/b.py']
    assert record['outcomes']['tests/w.py::t'] == 'not-covered'
    assert set(record['outcomes'].values()) == {'passed', 'not-covered'}
    assert record['overheads'] == {'tests/a.py': 5.0, 'tests/b.py': 5.0}
    assert record['peaks_mb'] == {'tests/a.py': 2048.0, 'tests/b.py': 2048.0}
    assert '#SBATCH --comment=lc:ws=ws-a' in ada.scripts[0]
    assert 'cd "$HOME"/ci/trees/' + SHA in ada.scripts[0]
    assert 'lab_ci_pytest_item' in json.dumps(ada.manifests[0])


def test_a_killed_shard_reruns_only_its_unfinished_ids_split_in_halves() -> None:
    ada = FakeAda(dies=frozenset({'tests/b.py'}))
    record = _verdict(ada)
    assert len(ada.manifests) == 2
    assert [i['ids'] for i in ada.manifests[1]['items']] == [['tests/b.py::t4'], ['tests/b.py::t5']]
    assert all(record['outcomes'][f'tests/b.py::t{n}'] == 'passed' for n in (3, 4, 5))
    assert len(record['rounds']) == 2


def test_what_is_still_unfinished_after_the_retries_is_lost() -> None:
    record = _verdict(FakeAda(dies=frozenset({'tests/b.py'}), kills=1 + RETRIES))
    assert len(record['rounds']) == 1 + RETRIES
    assert record['outcomes']['tests/b.py::t3'] == 'passed', 'what a killed shard finished is kept'
    assert [record['outcomes'][f'tests/b.py::t{n}'] for n in (4, 5)] == ['lost', 'lost']


def test_an_out_of_memory_round_doubles_the_memory_of_the_next() -> None:
    record = _verdict(FakeAda(dies=frozenset({'tests/b.py'}), oom=True))
    first, second = record['rounds']
    assert second['mem_mb'] == 2 * first['mem_mb']


def test_history_prices_the_plan_and_names_nothing_unmeasured() -> None:
    history = {
        'durations': {f'tests/{f}.py::t{n}': 30.0 for f, n in (('a', 1), ('a', 2), ('b', 3), ('b', 4), ('b', 5))},
        'overheads': {'tests/a.py': 20.0, 'tests/b.py': 20.0},
        'peaks_mb': {'tests/a.py': 1000.0, 'tests/b.py': 3000.0},
    }
    record = _verdict(FakeAda(), history=history)
    assert record['plan']['unmeasured'] == []
    assert record['plan']['mem_mb'] == -(-3000 * 1.5 // 1)


def test_a_commit_off_the_remote_without_a_bundle_is_refused() -> None:
    with pytest.raises(RuntimeError, match=r'not on https://g/r\.git'):
        remote_verdict(
            VerdictSpec(sha=SHA, repo_url='https://g/r.git', install='true'),
            Machine(workstation='w', grants=(GRANT,)),
            lambda _g: FakeAda(have=False),
            cost=Cost(),
            policy=Policy(),
        )
