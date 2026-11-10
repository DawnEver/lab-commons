"""An item is ONE pytest process over all its files: its memory and its wall are sized as a whole.

dev-lab #493 (one account, 7 days): ~10 tasks a downstream ``base`` run died at 1-2 GB running 8-121 files whose
learned peaks were 0.5-0.8 GB -- the ask was the heaviest file's, the process held them all. And a TIMEOUT
raised only the id the wall cut, so an item killed at 57 min was re-asked short and killed again at 110 min.
"""

from __future__ import annotations

import json

import pytest

from lab_commons.compute.measured import (
    HEAVY_SHARE,
    MEASURED_GROWTH,
    OOM_GROWTH,
    TIMEOUT_GROWTH,
    Measured,
    file_of,
    killed_need_mb,
    memory_band,
)
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.pytest_item import read_stream

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
    assert measured.floors == {'a.py::cut': 600.0}, 'no close line names the kill: the floor is what it ran'


# -- a killed item learns what it needed (run 4fb97faa904f, 2026-10-30) ----------------------------------------

_SDM = 'tests/unit/workflow/test_back_emf_full_matrix.py::test_back_emf[spm_slotted-sdm-A1]'
_FEA = 'tests/unit/workflow/test_back_emf_full_matrix.py::test_back_emf[spm_slotted-fea-A1]'


def _stream(*lines: dict[str, object]) -> str:
    """An item's ``.lab-ci/*-r*.jsonl`` stream, as the item plugin writes it."""
    return '\n'.join(json.dumps(line) for line in lines)


def _killed_at_498s(ids: list[str], peak_mb: float = 1351.0) -> dict[str, object]:
    """The recorded shape: the fea id passed in 3.1 s, the sdm id started and the cgroup OOM-killed the item."""
    return read_stream(
        _stream(
            {'start': ids[0]},
            {'id': ids[0], 'outcome': 'passed', 's': 3.1},
            {'start': ids[1]},
            {'done': -9, 'wall': 498.0, 'peak_mb': peak_mb},
        ),
        ids,
    )


def _killed_alone(node: str, peak_mb: float) -> dict[str, object]:
    """A LONE id's item, OOM-killed while it ran: the one shape a kill teaches a memory floor from."""
    return read_stream(_stream({'start': node}, {'done': -9, 'wall': 498.0, 'peak_mb': peak_mb}), [node])


def test_an_oom_killed_item_floors_its_cut_id_at_the_wall_it_died_at_grown() -> None:
    """Run 4fb97faa904f: ``back_emf[spm_slotted-sdm-A1]`` ran 498 s before its OOM kill and learned a 38.3 s floor.

    ``learn`` booked the whole killed wall as the file's overhead, ``killed`` subtracted it back as explained.
    """
    ids = [_FEA, _SDM]
    measured = Measured()
    folded = _killed_at_498s(ids)
    measured.learn(ids, folded)
    measured.killed(ids, folded, spent=498.0)
    assert measured.overheads == {}, 'a killed wall is no overhead: nothing in it closed'
    assert measured.floors[_SDM] >= (498.0 - 3.1) * TIMEOUT_GROWTH
    assert sum(measured.estimate([[_SDM]], _COST)[0]) >= (498.0 - 3.1) * TIMEOUT_GROWTH


def test_a_finished_item_still_learns_its_true_overhead() -> None:
    """PLANTED CONTROL: the same item CLOSING (``done`` 0) books its wall minus its tests as the file's overhead."""
    ids = [_FEA, _SDM]
    measured = Measured()
    stream = _stream(
        {'start': _FEA},
        {'id': _FEA, 'outcome': 'passed', 's': 3.1},
        {'start': _SDM},
        {'id': _SDM, 'outcome': 'passed', 's': 450.0},
        {'done': 0, 'wall': 498.0, 'peak_mb': 2100.0},
    )
    measured.learn(ids, read_stream(stream, ids))
    assert measured.overheads == {file_of(_SDM): pytest.approx(498.0 - 453.1, rel=1e-12, abs=1e-9)}


def test_a_heavy_id_in_a_light_parametrized_file_is_priced_by_its_own_peak_floor() -> None:
    """Run 4fb97faa904f: the sdm cases of ``test_back_emf_full_matrix.py`` need 2.1-6.4 GB; the file is 1351 MB.

    A per-FILE peak cannot say it; a per-ID peak floor does, and only for the id it names.
    """
    measured = Measured(peaks_mb={file_of(_SDM): 1351.0}, id_peaks_mb={_SDM: 6400.0})
    policy = Policy(safety=1.5)
    assert measured.item_gb([_SDM], _COST, policy) == pytest.approx(6400.0 * 1.5 / 1024, rel=1e-12, abs=0.0)
    assert measured.item_gb([_FEA], _COST, policy) == pytest.approx(1351.0 * 1.5 / 1024, rel=1e-12, abs=0.0), (
        'PLANTED CONTROL: its light sibling keeps the file peak'
    )
    assert Measured.of(measured.record()).id_peaks_mb == {_SDM: 6400.0}, 'the record carries it'


def test_an_id_that_finishes_drops_its_peak_floor() -> None:
    """Like a duration floor: once the id finished, its file's measured peak covers it."""
    measured = Measured(id_peaks_mb={_SDM: 6400.0, _FEA: 3000.0})
    stream = _stream({'id': _SDM, 'outcome': 'passed', 's': 450.0}, {'done': 0, 'wall': 460.0, 'peak_mb': 5000.0})
    measured.learn([_SDM], read_stream(stream, [_SDM]))
    assert measured.id_peaks_mb == {_FEA: 3000.0}
    assert measured.peaks_mb == {file_of(_SDM): 5000.0}


def test_a_lone_id_oom_kill_floors_its_files_peak_at_the_limit_grown() -> None:
    """Run 4fb97faa904f: a one-file kill taught the ~limit it read, so the next run asked limit x 1.5 and died again.

    The read is the limit the cgroup killed at, not the need: the lone id's file's floor is that times MEASURED_GROWTH.
    """
    measured = Measured(peaks_mb={file_of(_SDM): 900.0})
    measured.learn([_SDM], _killed_alone(_SDM, peak_mb=1351.0))
    assert measured.peaks_mb == {file_of(_SDM): 1351.0 * MEASURED_GROWTH}
    known = Measured(peaks_mb={file_of(_SDM): 4000.0})
    known.learn([_SDM], _killed_alone(_SDM, peak_mb=1351.0))
    assert known.peaks_mb == {file_of(_SDM): 4000.0}, 'PLANTED CONTROL: a kill below a known peak lowers nothing'


def test_an_oom_kill_is_a_lower_bound_a_finished_sibling_never_lowers() -> None:
    """Ada 2026-11-02/03, run 4c4cf161110e-base: asks of 1873, 1819, 1407, 1416, 1337 MB, each OOM-killed.

    A one-file kill floored only the FILE's peak, and the next round's fold -- a fresh instance -- let a
    finished sibling item of that file (a subset of its ids) replace it with its own lower read, so the
    cut id was re-asked below the limit it had already died at. The lone killed id carries the floor itself.
    """
    killed = Measured(peaks_mb={file_of(_SDM): 900.0})
    killed.learn([_SDM], _killed_alone(_SDM, peak_mb=1873.0))
    later = Measured.of(killed.record())
    sibling = _stream(
        {'start': _FEA}, {'id': _FEA, 'outcome': 'passed', 's': 3.0}, {'done': 0, 'wall': 9.0, 'peak_mb': 700.0}
    )
    later.learn([_FEA], read_stream(sibling, [_FEA]))
    policy = Policy(safety=1.0)
    assert later.item_gb([_SDM], _COST, policy) >= 1873.0 * MEASURED_GROWTH / 1024
    assert later.item_gb([_FEA], _COST, policy) == pytest.approx(700.0 / 1024, rel=1e-12, abs=0.0), (
        'PLANTED CONTROL: the finished sibling is priced by its own read'
    )


def test_a_killed_item_of_several_ids_floors_nothing_and_a_lone_id_floors_itself_and_its_file() -> None:
    """ISOLATE BEFORE GROWING: a kill shared by several ids is no id's or file's measurement; the retry splits it.

    PLANTED CONTROL: the same kill of ONE id floors that id and its file at the grown read.
    """
    ids = [_SDM, f'{file_of(_SDM)}::test_other']
    measured = Measured(peaks_mb={file_of(_SDM): 900.0})
    measured.learn(ids, _killed_at_498s(ids, peak_mb=2048.0))
    assert measured.id_peaks_mb == {}
    assert measured.peaks_mb == {file_of(_SDM): 900.0}
    assert measured.taught == set()
    measured.learn([_SDM], _killed_alone(_SDM, peak_mb=2048.0))
    assert measured.id_peaks_mb == {_SDM: 2048.0 * MEASURED_GROWTH}
    assert measured.peaks_mb == {file_of(_SDM): 2048.0 * MEASURED_GROWTH}


def test_a_kill_grows_from_its_measured_peak_by_a_bounded_factor_and_doubles_only_unmeasured() -> None:
    """GROW FROM MEASUREMENT: a kill that read its peak asks that (never under its limit) x MEASURED_GROWTH.

    Doubling a measured kill re-asks twice a need already read (Ada 2026-11-08: asks grown to the ceiling).
    Only a kill with NO read -- its need unknown past the limit -- grows by OOM_GROWTH. The factor is bounded:
    above 1 (an ask at the kill dies again) and no more than the blind growth.
    """
    assert 1.0 < MEASURED_GROWTH < OOM_GROWTH
    assert killed_need_mb(3000.0, 2048.0) == pytest.approx(3000.0 * MEASURED_GROWTH, rel=1e-12, abs=0.0)
    assert killed_need_mb(435.0, 870.0) == pytest.approx(870.0 * MEASURED_GROWTH, rel=1e-12, abs=0.0)
    assert killed_need_mb(0.0, 2048.0) == pytest.approx(2048.0 * OOM_GROWTH, rel=1e-12, abs=0.0)
    measured = Measured()
    measured.learn([_SDM], _killed_alone(_SDM, peak_mb=1351.0), allowed_mb=1024.0)
    assert measured.id_peaks_mb == {_SDM: pytest.approx(1351.0 * MEASURED_GROWTH, rel=1e-12, abs=0.0)}


def test_a_heavy_file_is_packed_alone_and_the_run_peak_is_priced_without_it() -> None:
    """HEAVY ISOLATION: a file whose ask exceeds HEAVY_SHARE of the grant never joins the shared pack.

    One heavy file sized the part's demand, so every base waited for room only it needed (Ada 2026-11-08).
    It runs as its own item, and the run's measured peak -- the part's demand -- is priced without it.
    PLANTED CONTROL: light files still share an item.
    """
    heavy, light = 'tests/heavy.py', ['tests/a.py', 'tests/b.py']
    measured = Measured(peaks_mb={heavy: 100_000.0, **dict.fromkeys(light, 500.0)})
    cost, policy = Cost(seconds=1.0, mem_gb=0.1), Policy(safety=1.0)
    assert HEAVY_SHARE == 0.25
    alone = measured.heavy([[f'{heavy}::t'], *([f'{n}::t'] for n in light)], cost, policy, ceiling_gb=360.0)
    assert alone == {heavy}
    items = measured.pack([[f'{n}::t'] for n in [*light, heavy]], cost, budget=1e9, alone=alone)
    assert items == [[f'{light[0]}::t', f'{light[1]}::t'], [f'{heavy}::t']]
    nodes = [f'{n}::t' for n in [*light, heavy]]
    assert measured.peak_gb(nodes, without=alone) == pytest.approx(500.0 / 1024, rel=1e-12, abs=0.0)
    assert measured.heavy([[f'{heavy}::t']], cost, policy, ceiling_gb=None) == set(), 'no ceiling, no share'
    from lab_commons.hpc.records import job_measured  # noqa: PLC0415 -- the part's demand, read where it is made

    run = job_measured([], nodes, measured, cpus=1, heavy=alone)
    assert run.peak_mem_gb == pytest.approx(500.0 / 1024, rel=1e-12, abs=0.0), 'the part demand leaves it out'
