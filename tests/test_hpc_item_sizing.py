"""An item is ONE pytest process over all its files: its memory and its wall are sized as a whole.

dev-lab #493 (ezzls2, 7 days): ~10 tasks a run of motronics ``base`` died at 1-2 GB running 8-121 files whose
learned peaks were 0.5-0.8 GB -- the ask was the heaviest file's, the process held them all. And a TIMEOUT
raised only the id the wall cut, so an item killed at 57 min was re-asked short and killed again at 110 min.
"""

from __future__ import annotations

import pytest

from lab_commons.compute.measured import Measured, memory_band
from lab_commons.hpc.config import Cost, Policy

_COST, _POLICY = Cost(seconds=120, mem_gb=0.1), Policy(safety=1.0)


def test_an_items_memory_accumulates_each_files_growth_over_the_shared_base() -> None:
    """PLANTED: three files at 500/700/800 MB; the process holds the base once.

    The lightest is the base; each other file adds its growth over it:
    800 + (700 - 500) + (500 - 500) = 1000 MB, not the worst file's 800.
    """
    measured = Measured(peaks_mb={'a.py': 500.0, 'b.py': 700.0, 'c.py': 800.0})
    ids = ['a.py::t', 'b.py::t', 'c.py::t']
    assert measured.item_gb(ids, _COST, _POLICY) == pytest.approx(1000.0 / 1024, rel=1e-12, abs=0.0)
    assert measured.item_gb(['c.py::t'], _COST, _POLICY) == pytest.approx(800.0 / 1024, rel=1e-12, abs=0.0), (
        'PLANTED CONTROL: one file is its own peak'
    )


def test_files_taught_one_items_peak_are_one_measurement_not_many() -> None:
    """A finished multi-file item teaches its peak to every file: that is ONE process read, counted once."""
    measured = Measured(peaks_mb={f'f{n}.py': 3000.0 for n in range(56)} | {'g.py': 3500.0})
    ids = [f'f{n}.py::t' for n in range(56)]
    assert measured.item_gb(ids, _COST, _POLICY) == pytest.approx(3000.0 / 1024, rel=1e-12, abs=0.0)
    both = Measured(peaks_mb={'a.py': 3000.0, 'b.py': 3000.0, 'g.py': 3500.0})
    assert both.item_gb(['a.py::t', 'b.py::t', 'g.py::t'], _COST, _POLICY) == pytest.approx(
        3500.0 / 1024, rel=1e-12, abs=0.0
    ), 'the base is the lightest read: 3500 + (3000 - 3000)'


def test_packing_never_lets_accumulation_climb_more_than_one_memory_class() -> None:
    """PLANTED: twenty 0.5-0.8 GB files, one class by their own peaks.

    Packed by time alone they share one item asking ~6.5 GB;
    packed by class they split so no item's accumulated ask exceeds the next class.
    """
    peaks = {f'f{n:02}.py': 512.0 + 15.0 * n for n in range(20)}
    measured = Measured(durations={f'{f}::t': 1.0 for f in peaks}, overheads=dict.fromkeys(peaks, 1.0), peaks_mb=peaks)
    cost, policy = Cost(seconds=120, mem_gb=0.1), Policy(safety=1.0)
    files = [[f'{f}::t'] for f in peaks]

    def band(ids: list[str]) -> int:
        return memory_band(measured.item_gb(ids, cost, policy))

    assert {band(ids) for ids in files} == {0}
    assert len(measured.pack(files, cost, budget=1e9)) == 1, 'PLANTED CONTROL: time alone packs them all'
    items = measured.pack(files, cost, budget=1e9, band=band)
    assert len(items) > 1
    assert max(band(ids) for ids in items) <= 1
    assert sorted(n for ids in items for n in ids) == sorted(n for ids in files for n in ids)


def test_a_timed_out_item_is_reasked_at_least_its_death_wall_grown() -> None:
    """Killed by the wall at 57 min with three never-started ids priced at 10 s: its retry must ask >= 2 x 57 min."""
    ids = ['a.py::done', 'a.py::cut', 'a.py::b', 'a.py::c', 'a.py::d']
    measured = Measured(durations={'a.py::done': 60.0, 'a.py::b': 10.0, 'a.py::c': 10.0, 'a.py::d': 10.0})
    folded = {'cells': {'a.py::done': {}}, 'seconds': {'a.py::done': 60.0}, 'done': None}
    measured.killed(ids, folded, spent=57 * 60.0, timed_out=True, top=48 * 3600.0)
    rest = ids[1:]
    assert sum(measured.estimate([rest], _COST)[0]) >= 2 * 57 * 60.0
    again = Measured.of(measured.record())
    assert sum(again.estimate([rest], _COST)[0]) >= 2 * 57 * 60.0, 'the floors are what the record carries'


def test_a_timed_out_items_growth_is_held_to_the_partition_max() -> None:
    ids = ['a.py::cut', 'a.py::b']
    measured = Measured()
    folded = {'cells': {}, 'seconds': {}, 'done': None}
    measured.killed(ids, folded, spent=2000 * 60.0, timed_out=True, top=2880 * 60.0)
    assert sum(measured.estimate([ids], _COST)[0]) == pytest.approx(2880 * 60.0, rel=1e-12, abs=0.0)


def test_an_oom_kill_still_floors_only_the_interrupted_id() -> None:
    """PLANTED CONTROL: a kill that is no TIMEOUT says nothing about the ids after the cut."""
    measured = Measured()
    folded = {'cells': {}, 'seconds': {}, 'done': None}
    measured.killed(['a.py::cut', 'a.py::b'], folded, spent=600.0)
    assert measured.floors == {'a.py::cut': 600.0}
