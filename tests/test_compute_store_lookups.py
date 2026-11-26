"""Every cache lookup is logged, and a miss says WHY (v1 ruling D125 F3, D41).

A hit rate with no miss reason cannot tell a broken cache from a changed input. :meth:`ResultStore.lookup` asks
for a UNIT (not a bare hash), so the store knows the unit's facts; it appends one JSONL line per lookup to
``<root>/lookups.jsonl`` with OpenTelemetry-style names, and a miss names the facts that differ from the nearest
unit of the same engine and entry it has seen -- ``version``, ``files.<name>``, ``params.<name>`` -- or says the unit
is new, was invalidated (with the tombstone's reason) or was evicted.
"""

from __future__ import annotations

import json
from pathlib import Path

from lab_commons.compute.model import Demand
from lab_commons.compute.store import LOOKUPS, Record, Result, ResultStore
from lab_commons.compute.unit import Engine, Unit


def _unit(version: str = '1', rpm: float = 3000.0, mesh: Path | None = None) -> Unit:
    inputs: dict[str, object] = {'rpm': rpm} if mesh is None else {'rpm': rpm, 'mesh': mesh}
    return Unit(Engine('vendor.solve', version), 'pkg.mod:fn', inputs, ('out.txt',), Demand(cpu=1, mem_gb=0.1), 60)


def _put(store: ResultStore, unit: Unit, files: dict[str, Path]) -> Result:
    record = Record(
        unit.unit_hash(files), unit.engine.name, unit.version, 'env', 'h1', 't0', 't1', '', local_only=False
    )
    return store.put_files({'out.txt': b'x'}, record)


def _lines(store: ResultStore) -> list[dict[str, object]]:
    return [json.loads(line) for line in (store.root / LOOKUPS).read_text(encoding='utf-8').splitlines()]


def test_a_hit_and_a_first_miss_are_logged(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    unit = _unit()
    assert store.lookup(unit, {}) is None
    _put(store, unit, {})
    assert isinstance(store.lookup(unit, {}), Result)
    miss, hit = _lines(store)
    assert miss['cache.result'] == 'miss'
    assert miss['cache.miss.reason'] == 'new'
    assert miss['cache.key'] == unit.unit_hash({})
    assert hit['cache.result'] == 'hit'
    assert hit['cache.tier'] == 'local'
    assert hit['code.function'] == 'pkg.mod:fn'
    assert hit['event.name'] == 'cache.lookup'


def test_a_miss_names_the_engine_version_that_changed(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    _put(store, _unit('1'), {})
    store.lookup(_unit('1'), {})
    assert store.lookup(_unit('2'), {}) is None
    miss = _lines(store)[-1]
    assert miss['cache.miss.reason'] == 'changed'
    assert miss['cache.miss.changed'] == ['version']


def test_a_miss_names_the_input_file_and_parameter_that_changed(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    mesh = tmp_path / 'a.msh'
    mesh.write_text('one', encoding='utf-8')
    store.lookup(_unit(mesh=mesh), {'mesh': mesh})
    mesh.write_text('two', encoding='utf-8')
    store.lookup(_unit(mesh=mesh), {'mesh': mesh})
    assert _lines(store)[-1]['cache.miss.changed'] == ['files.mesh']
    store.lookup(_unit(rpm=4000.0, mesh=mesh), {'mesh': mesh})
    assert _lines(store)[-1]['cache.miss.changed'] == ['params.rpm']


def test_a_miss_after_invalidation_carries_the_tombstones_reason(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    unit = _unit()
    _put(store, unit, {})
    store.lookup(unit, {})
    store.invalidate(unit.unit_hash({}), 'wrong material card', host='h1')
    store.lookup(unit, {})
    miss = _lines(store)[-1]
    assert miss['cache.miss.reason'] == 'invalidated'
    assert miss['cache.miss.detail'] == 'wrong material card'


def test_a_seen_unit_with_no_result_is_named_not_new(tmp_path: Path) -> None:
    """PLANTED CONTROL: a unit looked up before but never stored (or evicted) is not reported as new."""
    store = ResultStore(tmp_path / 'store')
    unit = _unit()
    store.lookup(unit, {})
    store.lookup(unit, {})
    assert [line['cache.miss.reason'] for line in _lines(store)] == ['new', 'absent']
