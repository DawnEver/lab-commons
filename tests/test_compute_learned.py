"""The one measured demand table: per test (shard and worker pricing) and per (producer, need-set) job."""

from __future__ import annotations

import pytest

from lab_commons.compute import learned
from lab_commons.compute.learned import OOM_GROWTH, Measured, Run
from lab_commons.compute.model import Demand

REPO = 'https://g/r.git'


def test_an_unmeasured_job_is_its_prior_and_a_measured_one_its_last_run() -> None:
    prior = Run(peak_mem_gb=1.0, cpu_s=60.0, wall_s=60.0)
    assert learned.demand(REPO, 'linux', {'jmag'}, prior=prior) == Demand(cpu=1, mem_gb=1.0, jmag=1)
    learned.record_run(REPO, 'linux', {'jmag'}, Run(peak_mem_gb=6.0, cpu_s=4 * 3600.0, wall_s=3600.0))
    assert learned.demand(REPO, 'linux', {'jmag'}, prior=prior) == Demand(cpu=4, mem_gb=6.0, jmag=1)
    assert learned.demand(REPO, 'linux', (), prior=prior) == Demand(cpu=1, mem_gb=1.0), (
        'PLANTED CONTROL: another need-set of the same producer learned nothing'
    )
    assert learned.demand('https://g/other.git', 'linux', {'jmag'}, prior=prior)['mem_gb'] == 1.0


def test_a_run_carries_its_cpu_hours_and_refuses_an_impossible_measurement() -> None:
    assert Run(peak_mem_gb=2.0, cpu_s=7200.0, wall_s=900.0).cpu_hours == 2.0
    with pytest.raises(ValueError, match='positive wall'):
        Run(peak_mem_gb=1.0, cpu_s=1.0, wall_s=0.0)


def test_a_killed_runs_peak_is_a_floor_that_never_lowers_what_is_known() -> None:
    learned.record_run(REPO, 'windows', (), Run(peak_mem_gb=8.0, cpu_s=10.0, wall_s=10.0))
    learned.record_run(REPO, 'windows', (), Run(peak_mem_gb=1.0, cpu_s=10.0, wall_s=10.0), killed=True)
    assert learned.run_of(REPO, 'windows', ()).peak_mem_gb == 8.0
    learned.record_run(REPO, 'windows', (), Run(peak_mem_gb=5.0, cpu_s=10.0, wall_s=10.0), killed=True)
    assert learned.run_of(REPO, 'windows', ()).peak_mem_gb == 5.0 * OOM_GROWTH
    learned.record_run(REPO, 'windows', (), Run(peak_mem_gb=3.0, cpu_s=10.0, wall_s=10.0))
    assert learned.run_of(REPO, 'windows', ()).peak_mem_gb == 3.0, 'a finished run replaces; nothing is averaged'


def test_per_test_facts_and_per_job_runs_share_one_file_without_erasing_each_other() -> None:
    learned.record_run(REPO, 'linux', (), Run(peak_mem_gb=2.0, cpu_s=1.0, wall_s=1.0))
    learned.learn({'repo': REPO, 'durations': {'a.py::x': 3.0}, 'peaks_mb': {'a.py': 512.0}})
    assert learned.run_of(REPO, 'linux', ()) is not None, 'learning test facts kept the job runs'
    learned.learn_peaks(REPO, {'b.py': 2048.0})
    table = learned.learned(REPO)
    assert table['durations'] == {'a.py::x': 3.0}
    assert table['peaks_mb'] == {'a.py': 512.0, 'b.py': 2048.0}
    assert sorted(path.name for path in learned.directory().iterdir()) == [learned._path(REPO).name]


def test_one_worker_rule_prices_a_box_worker_and_a_slurm_task_alike() -> None:
    learned.learn_peaks(REPO, {'tests/a.py': 1024.0, 'tests/b.py': 3072.0})
    assert learned.worker_gb(REPO, ['tests/a.py'], prior_gb=0.5, safety=1.5) == 1.5
    assert learned.worker_gb(REPO, ['tests/a.py::t', 'tests/b.py::u'], prior_gb=0.5, safety=1.5) == 4.5
    assert learned.worker_gb(REPO, ['tests/new.py'], prior_gb=0.5, safety=1.5) == 0.5, 'unmeasured is the prior'
    measured = Measured.of(learned.learned(REPO))
    assert measured.worker_gb(['tests/b.py'], prior_gb=1.0, safety=1.5) == 4.5


def test_what_serve_admits_against_is_keyed_by_the_need_set_it_ran() -> None:
    """A box part is learned under (local, its need-set) and read back under the same key -- not under its OS.

    PLANTED CONTROL: the base part learned nothing, so it stays the whole-box prior; a cluster reads slurm runs.
    """
    learned.record_run(REPO, learned.LOCAL, {'jmag'}, Run(peak_mem_gb=6.0, cpu_s=2 * 3600.0, wall_s=3600.0))
    box = learned.learned_demand(REPO, cluster=False, cores=8)
    assert box(frozenset({'jmag'})) == Demand(cpu=2, mem_gb=6.0, jmag=1)
    assert box(frozenset()) == Demand(cpu=8, mem_gb=0.0)
    assert learned.learned_demand(REPO, cluster=True, cores=8)(frozenset({'jmag'})) == Demand(cpu=1, mem_gb=0.0, jmag=1)
    assert learned.producer_of(cluster=True) == learned.SLURM
