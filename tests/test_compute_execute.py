"""D16/D19: the local executor runs units in-process under the box ledger; every rule has a planted control.

The entries are this module's own functions, named ``<module>:<function>`` exactly as a production entry is.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from _box_fake import quiet
from test_compute_store import Shared

from lab_commons.box import BOX_ENV, Capacity
from lab_commons.box.ledger import hold, holdings
from lab_commons.compute import execute as execute_module
from lab_commons.compute.execute import (
    CACHED,
    FAILED,
    OFFERED,
    PLACEMENT,
    RAN,
    REFUSED,
    SKIPPED,
    Hold,
    LocalExecutor,
    Offer,
    Where,
    presence_check,
)
from lab_commons.compute.graph import Graph
from lab_commons.compute.model import Demand
from lab_commons.compute.presence import Evidence, Presence
from lab_commons.compute.store import ResultStore
from lab_commons.compute.unit import Engine, Environment, Output, Unit

ENV = Environment('a' * 40, 'k' * 24)
DIRTY = Environment('a' * 40, 'k' * 24, 'd' * 64)
SMALL = Demand(cpu=1, mem_gb=0.1)
CALLS: list[str] = []


class Partitioned(Shared):
    """A host that cannot read the shared tier when it looks, and posts once it can: a race, a partition."""

    def fetch(self, unit_hash: str, dest: Path) -> Path | None:
        del unit_hash, dest
        return None


class Crash(BaseException):
    """The process dying mid-run: nothing in the executor may catch it."""


def write(inputs: Mapping[str, Any], work: Path) -> None:
    CALLS.append('write')
    (work / 'out').write_text(str(inputs['n']), encoding='utf-8')


def double(inputs: Mapping[str, Any], work: Path) -> None:
    CALLS.append('double')
    (work / 'out').write_text(str(2 * int(inputs['x'].read_text(encoding='utf-8'))), encoding='utf-8')


def boom(_inputs: Mapping[str, Any], _work: Path) -> None:
    CALLS.append('boom')
    msg = 'the solver diverged'
    raise ArithmeticError(msg)


def nothing(_inputs: Mapping[str, Any], _work: Path) -> None:
    CALLS.append('nothing')


def counter(_inputs: Mapping[str, Any], work: Path) -> None:
    CALLS.append('counter')
    (work / 'out').write_text(str(len(CALLS)), encoding='utf-8')


def march(_inputs: Mapping[str, Any], work: Path) -> None:
    """Steps 1..4, one checkpoint file per step; dies after step 2 the first time it runs."""
    checkpoint = work / 'checkpoint'
    step = int(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else 0
    CALLS.append(f'march from {step}')
    while step < 4:
        step += 1
        checkpoint.write_text(str(step), encoding='utf-8')
        if step == 2 and len(CALLS) == 1:
            raise Crash
    (work / 'out').write_text(str(step), encoding='utf-8')


def unit(fn: str, *, env: Environment = ENV, demand: Demand = SMALL, **inputs: object) -> Unit:
    return Unit(Engine.ours('t.solve', env), f'{__name__}:{fn}', inputs, ('out',), demand, 60)


@contextmanager
def free_hold(
    demand: Demand, wall_s: int, what: str, queue_s: float, *, key: str, held: list[Demand]
) -> Iterator[None]:
    assert (wall_s, what, queue_s, len(key)) == (60, what, queue_s, 64)
    held.append(demand)
    yield


@pytest.fixture(autouse=True)
def _calls() -> None:
    CALLS.clear()


def executor(
    tmp_path: Path,
    *,
    shared: Shared | None = None,
    held: list[Demand] | None = None,
    hold: Hold | None = None,
    present: Callable[[Engine], str] = lambda _e: '',
    where: Where = Where.HERE,
    offer: Offer | None = None,
) -> LocalExecutor:
    return LocalExecutor(
        ResultStore(tmp_path / 'store', shared),
        tmp_path / 'journal',
        present=present,
        where=where,
        offer=offer,
        hold=hold or partial(free_hold, held=[] if held is None else held),
    )


def test_a_graph_runs_in_order_under_the_ledger_and_a_rerun_is_a_cache_hit(tmp_path: Path) -> None:
    graph = Graph({'a': unit('write', n=21), 'b': unit('double', x=Output('a', 'out'))})
    held: list[Demand] = []
    ex = executor(tmp_path, held=held)
    first = ex.run(graph)
    assert [first[n].state for n in 'ab'] == [RAN, RAN]
    assert first['b'].result.output('out').read_text(encoding='utf-8') == '42'
    assert held == [SMALL, SMALL]
    again = ex.run(graph)
    assert [again[n].state for n in 'ab'] == [CACHED, CACHED]
    assert CALLS == ['write', 'double']  # THE CHECK: a cache hit runs nothing
    changed = ex.run(Graph({'a': unit('write', n=22), 'b': unit('double', x=Output('a', 'out'))}))
    assert [changed[n].state for n in 'ab'] == [RAN, RAN]  # PLANTED: another input is another unit
    assert changed['b'].result.output('out').read_text(encoding='utf-8') == '44'


def test_a_failure_is_named_and_its_dependants_do_not_run(tmp_path: Path) -> None:
    graph = Graph(
        {'a': unit('boom'), 'b': unit('double', x=Output('a', 'out')), 'c': unit('write', n=1), 'd': unit('nothing')}
    )
    got = executor(tmp_path).run(graph)
    assert got['a'].state == FAILED
    assert 'a: ArithmeticError: the solver diverged' in got['a'].why
    assert (got['b'].state, got['b'].why) == (SKIPPED, 'upstream a failed')
    assert got['c'].state == RAN  # PLANTED: an independent unit still runs
    assert got['d'].state == FAILED
    assert 'declared outputs not written' in got['d'].why
    assert 'double' not in CALLS
    assert executor(tmp_path).run(graph)['a'].state == FAILED  # a failed unit is a cache miss: it runs again
    assert CALLS.count('boom') == 2


def test_a_crash_mid_run_resumes_from_the_journal(tmp_path: Path) -> None:
    graph = Graph({'m': unit('march')})
    with pytest.raises(Crash):
        executor(tmp_path).run(graph)
    survivor = executor(tmp_path)
    assert len(survivor.pending()) == 1  # the intent outlived the process
    got = survivor.run(graph)['m']
    assert (got.state, got.resumed) == (RAN, True)
    assert CALLS == ['march from 0', 'march from 2']  # resumed at its checkpoint, not restarted
    assert got.result.output('out').read_text(encoding='utf-8') == '4'
    assert survivor.pending() == ()
    assert not (tmp_path / 'journal' / 'work' / got.unit_hash).exists()
    assert executor(tmp_path).run(graph)['m'].resumed is False  # PLANTED: a clean cache hit is no resume


def test_a_differing_duplicate_alarms(tmp_path: Path) -> None:
    (tmp_path / 'forge').mkdir()
    graph = Graph({'n': unit('counter')})
    assert executor(tmp_path / 'one', shared=Shared(tmp_path / 'forge')).run(graph)['n'].state == RAN
    seen = executor(tmp_path / 'seen', shared=Shared(tmp_path / 'forge')).run(graph)['n']
    assert seen.state == CACHED  # PLANTED: a host that can read the first result never runs it again
    blind = Partitioned(tmp_path / 'forge')
    other = executor(tmp_path / 'two', shared=blind).run(graph)['n']
    assert blind.posts == 1
    assert other.state == FAILED  # the same unit hash computed a different result: the first stands
    assert other.why.startswith('n: ')
    assert CALLS == ['counter', 'counter']


def test_a_dirty_tree_is_never_shared_nor_offered(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'forge')
    (tmp_path / 'forge').mkdir()
    assert executor(tmp_path / 'a', shared=shared).run(Graph({'w': unit('write', env=DIRTY, n=1)}))['w'].state == RAN
    assert shared.posts == 0
    assert executor(tmp_path / 'b', shared=shared).run(Graph({'w': unit('write', n=1)}))['w'].state == RAN
    assert shared.posts == 1  # PLANTED: the clean twin is shared
    offered: list[str] = []
    away = executor(tmp_path / 'c', where=Where.ELSEWHERE, offer=lambda _u, h, _f: offered.append(h) or 'job#1')
    got = away.run(Graph({'w': unit('write', env=DIRTY, n=2)}))['w']
    assert got.state == REFUSED
    assert 'local-only' in got.why
    assert offered == []


def test_where_is_data_and_decides_run_offer_or_refuse(tmp_path: Path) -> None:
    assert {a for (w, _), a in PLACEMENT.items() if w is Where.HERE} == {'run', 'refuse'}
    absent = executor(tmp_path / 'h', present=lambda e: f'{e.name} absent')
    got = absent.run(Graph({'w': unit('write', n=1)}))['w']
    assert (got.state, got.why) == (REFUSED, 'w: t.solve absent')
    offered: list[tuple[str, dict[str, Path]]] = []

    def offer(_unit: Unit, unit_hash: str, files: Mapping[str, Path]) -> str:
        offered.append((unit_hash, dict(files)))
        return 'job#7'

    anywhere = executor(tmp_path / 'a', present=lambda e: f'{e.name} absent', where=Where.ANYWHERE, offer=offer)
    graph = Graph({'w': unit('write', n=1), 'd': unit('double', x=Output('w', 'out'))})
    got = anywhere.run(graph)
    assert (got['w'].state, got['w'].job) == (OFFERED, 'job#7')
    assert got['d'].state == SKIPPED  # waits on the Job's result
    assert CALLS == []
    with pytest.raises(ValueError, match='no offer is wired'):
        LocalExecutor(ResultStore(tmp_path), tmp_path, present=lambda _e: '', where=Where.ANYWHERE)


def test_a_unit_larger_than_the_box_is_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    box = Capacity(cpu=2, mem_bytes=4 << 30, kept_cpu=0, kept_bytes=0, seats={})
    real = partial(hold, capacity=box, read=quiet(box))
    big = unit('write', demand=Demand(cpu=64, mem_gb=1.0), n=1)
    fits = unit('write', n=2)
    here = executor(tmp_path / 'h', hold=real)
    got = here.run(Graph({'big': big, 'fits': fits}))
    assert (got['big'].state, got['fits'].state) == (REFUSED, RAN)
    assert 'larger than this box' in got['big'].why
    there = executor(tmp_path / 'a', hold=real, where=Where.ANYWHERE, offer=lambda _u, _h, _f: 'job#9')
    assert there.run(Graph({'big': big}))['big'].job == 'job#9'


def test_presence_check_reads_environment_and_probed_version() -> None:
    probed = {'femm': Presence('femm', present=True, evidence=(Evidence('version: file', '5.0', 't'),))}
    why = presence_check(ENV, probed)
    assert why(Engine.ours('t.solve', ENV)) == ''
    assert 'needs environment' in why(Engine.ours('t.solve', DIRTY))
    assert why(Engine('femm', '5.0')) == ''
    assert why(Engine('femm', '5.1')) == 'femm 5.1 wanted, 5.0 here'
    assert 'not present' in why(Engine('jmag', '25'))


SPANS: dict[str, tuple[float, float]] = {}


def nap(inputs: Mapping[str, Any], work: Path) -> None:
    """Sleeps a moment, recording when it ran."""
    start = time.monotonic()
    time.sleep(0.4)
    SPANS[inputs['tag']] = (start, time.monotonic())
    (work / 'out').write_text(inputs['tag'], encoding='utf-8')


def interrupted(_inputs: Mapping[str, Any], _work: Path) -> None:
    time.sleep(0.2)  # long enough for the napping sibling to hold its claim
    raise KeyboardInterrupt


def _overlap(a: str, b: str) -> bool:
    return SPANS[a][0] < SPANS[b][1] and SPANS[b][0] < SPANS[a][1]


def ledger_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seats: Mapping[str, int]) -> LocalExecutor:
    monkeypatch.setenv(BOX_ENV, str(tmp_path / 'box.sqlite'))
    box = Capacity(cpu=4, mem_bytes=4 << 30, kept_cpu=0, kept_bytes=0, seats=dict(seats))
    real = partial(hold, capacity=box, read=quiet(box), poll_s=0.02)
    return LocalExecutor(
        ResultStore(tmp_path / 'store'), tmp_path / 'journal', present=lambda _e: '', hold=real, queue_s=30.0
    )


def test_ready_units_the_ledger_admits_run_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SPANS.clear()
    ex = ledger_executor(tmp_path, monkeypatch, {})
    graph = Graph(
        {'a': unit('nap', tag='a'), 'b': unit('nap', tag='b'), 'c': unit('nap', tag='c', x=Output('a', 'out'))}
    )
    got = ex.run(graph)
    assert {o.state for o in got.values()} == {RAN}
    assert list(got) == ['a', 'b', 'c']  # outcomes in graph order, whatever order they finished in
    assert _overlap('a', 'b')  # THE CHECK: two independent admissible units overlap in time
    assert SPANS['c'][0] >= SPANS['a'][1]  # PLANTED: a dependant waits for its input
    assert holdings() == []


def test_one_seat_serialises_two_seat_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SPANS.clear()
    seat = Demand({'jmag': 1, 'cpu': 1, 'mem_gb': 0.1})
    graph = Graph({'a': unit('nap', demand=seat, tag='a'), 'b': unit('nap', demand=seat, tag='b')})
    assert {o.state for o in ledger_executor(tmp_path, monkeypatch, {'jmag': 1}).run(graph).values()} == {RAN}
    assert not _overlap('a', 'b')  # the ledger, not a pool size, bounds a vendor's concurrency
    SPANS.clear()
    ledger_executor(tmp_path / 'two', monkeypatch, {'jmag': 2}).run(graph)
    assert _overlap('a', 'b')  # PLANTED: two seats admit both


def test_a_failure_stops_only_its_subtree_while_a_sibling_runs(tmp_path: Path) -> None:
    SPANS.clear()
    graph = Graph(
        {
            'a': unit('boom'),
            'b': unit('double', x=Output('a', 'out')),
            'c': unit('double', x=Output('b', 'out')),
            'd': unit('nap', tag='d'),
        }
    )
    got = executor(tmp_path).run(graph)
    assert [got[n].state for n in 'abcd'] == [FAILED, SKIPPED, SKIPPED, RAN]
    assert got['c'].why == 'upstream b skipped'


def test_two_units_of_one_hash_run_once(tmp_path: Path) -> None:
    got = executor(tmp_path).run(Graph({'a': unit('counter'), 'b': unit('counter')}))
    assert [got[n].state for n in 'ab'] == [RAN, CACHED]
    assert CALLS == ['counter']


def test_an_interrupt_releases_every_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SPANS.clear()
    ex = ledger_executor(tmp_path, monkeypatch, {})
    with pytest.raises(KeyboardInterrupt):
        ex.run(Graph({'i': unit('interrupted'), 'n': unit('nap', tag='n')}))
    assert 'n' not in SPANS  # PLANTED: the sibling was still running when run() returned
    assert holdings() == []  # THE CHECK: its claim is released anyway


def test_what_became_of_a_unit_is_named_settled_and_the_generic_name_is_gone() -> None:
    """Outcome is the task-result word lab_commons.outcomes owns (plan A1.10); a unit's end state is Settled."""
    assert 'Settled' in execute_module.__all__
    with pytest.raises(AttributeError):
        _ = importlib.import_module(execute_module.__name__).Outcome


def test_presence_check_re_probes_a_stale_reading_instead_of_refusing() -> None:
    stale = Presence(
        'jmag', present=False, evidence=(Evidence('version: designer -w', '25.2', 't'), Evidence('ttl', 'older', 't'))
    )
    fresh = Presence('jmag', present=True, evidence=(Evidence('version: designer -w', '25.2', 'u'),))
    asked: list[str] = []
    why = presence_check(None, {'jmag': stale}, renew=lambda name: asked.append(name) or fresh)
    assert why(Engine('jmag', '25.2')) == ''
    assert asked == ['jmag']
    failed = Presence('jmag', present=False, evidence=(Evidence('smoke', 'FAILED: exit 1', 'u'),))
    why = presence_check(None, {'jmag': stale}, renew=lambda _name: failed)
    assert 'smoke: FAILED: exit 1' in why(Engine('jmag', '25.2'))
    asked.clear()
    absent = Presence('jmag', present=False, evidence=(Evidence('find', 'not found', 't'),))
    assert 'not present' in presence_check(None, {'jmag': absent}, renew=asked.append)(Engine('jmag', '25.2'))
    assert asked == [], 'only a merely stale reading is re-probed'
