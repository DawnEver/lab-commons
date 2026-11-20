"""FORMATS-MIGRATE-ONCE for run states and records: migrated once, then a no-op; an unmigrated one refused."""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from lab_commons.compute.measured import Measured, UnmigratedRecord
from lab_commons.hpc.__main__ import main as hpc_main
from lab_commons.hpc.builds import ItemWall
from lab_commons.hpc.migrate import migrate_file
from lab_commons.hpc.run import item_walls, planned

#: A state written before ``item_wall`` and ``id_peaks_mb`` existed.
_OLD_STATE = {'sha': 'abc', 'measured': {'durations': {'a.py::x': 2.0}, 'floors': {}, 'overheads': {}, 'peaks_mb': {}}}


def _record(path: Path, record: dict[str, object]) -> Path:
    path.write_bytes(gzip.compress(json.dumps(record).encode('utf-8')))
    return path


def test_an_unmigrated_state_is_refused_naming_the_migration() -> None:
    with pytest.raises(UnmigratedRecord, match=r'item_wall.*python -m lab_commons.hpc migrate'):
        item_walls(['a.py::x'], _OLD_STATE)
    with pytest.raises(UnmigratedRecord, match=r'id_peaks_mb.*python -m lab_commons.hpc migrate'):
        planned({**_OLD_STATE, 'item_wall': asdict(ItemWall())})


def test_an_unmigrated_record_is_refused_naming_the_migration() -> None:
    with pytest.raises(UnmigratedRecord, match=r'id_peaks_mb.*python -m lab_commons.hpc migrate'):
        Measured.of({'durations': {}, 'floors': {}, 'overheads': {}, 'peaks_mb': {}})


def test_a_state_migrates_once_then_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(_OLD_STATE), encoding='utf-8')
    assert hpc_main(['migrate', str(path)]) == 0
    assert capsys.readouterr().out.startswith('migrated')
    state = json.loads(path.read_text(encoding='utf-8'))
    assert state['item_wall'] == asdict(ItemWall())
    assert state['measured']['durations'] == {'a.py::x': 2.0}, 'what was measured is kept'
    assert item_walls(['a.py::x'], state) == {'a.py::x': ItemWall().bound(2.0)}
    before = path.read_bytes()
    assert migrate_file(path) is False, 'a second run is a no-op'
    assert path.read_bytes() == before


def test_a_record_migrates_once_then_reads(tmp_path: Path) -> None:
    path = _record(tmp_path / 'run-abc-femm-1.json.gz', {'cells': {}, 'durations': {'a.py::x': 2.0}})
    assert migrate_file(path) is True
    record = json.loads(gzip.decompress(path.read_bytes()))
    assert Measured.of(record).durations == {'a.py::x': 2.0}
    before = path.read_bytes()
    assert migrate_file(path) is False
    assert path.read_bytes() == before


def test_a_current_record_is_planted_unchanged(tmp_path: Path) -> None:
    current = {'cells': {}, **Measured(durations={'a.py::x': 1.0}).record()}
    path = _record(tmp_path / 'run-abc-femm-2.json.gz', current)
    before = path.read_bytes()
    assert migrate_file(path) is False
    assert path.read_bytes() == before


def test_a_fact_bag_without_a_kind_is_none_learned_not_refused() -> None:
    """Planted beside the refusal: a box verify record carries no measurement and is current, not older."""
    box_record = {'cells': {}, 'platform': 'base', 'sha': 'abc'}
    assert Measured.carried(box_record) == Measured()
    with pytest.raises(UnmigratedRecord):
        Measured.of(box_record)
