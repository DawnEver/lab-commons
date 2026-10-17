"""The one measured demand table: per test (shard and worker pricing) and per (producer, need-set) job."""

from __future__ import annotations

import pytest

from lab_commons.compute import learned
from lab_commons.compute.admission import admit
from lab_commons.compute.learned import OOM_GROWTH, Measured, Run
from lab_commons.compute.model import Demand, Job, Resources, Supply

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
    assert learned.worker_gb(REPO, ['tests/new.py'], prior_gb=0.5, safety=1.5) == 3.0, 'its siblings median, 2 GB'
    measured = Measured.of(learned.learned(REPO))
    assert measured.worker_gb(['tests/b.py'], prior_gb=1.0, safety=1.5) == 4.5


def test_an_unmeasured_file_is_priced_at_its_measured_siblings_median_never_the_global_max() -> None:
    """D5: the prior of an unmeasured file is the median of its directory's measured files, else the table's.

    PLANTED CONTROL: a 16 GB file elsewhere in the table moves neither prior the way the global max would.
    """
    measured = Measured(peaks_mb={'tests/u/a.py': 100.0, 'tests/u/b.py': 300.0, 'tests/u/c.py': 200.0,
                                  'tests/heavy/h.py': 16384.0})  # fmt: skip
    assert measured.prior_mb('tests/u/new.py') == 200.0, 'the median of its three siblings'
    assert measured.prior_mb('tests/other/new.py') == 250.0, 'no sibling: the median of the whole table'
    assert Measured().prior_mb('tests/x.py') is None, 'an empty table has no prior'
    assert measured.worker_gb(['tests/u/new.py'], prior_gb=0.0, safety=1.0) == pytest.approx(200 / 1024, abs=1e-9)
    assert Measured().worker_gb(['tests/x.py'], prior_gb=0.5, safety=1.0) == 0.5, 'nothing measured: the floor'
    assert measured.peak_gb(['tests/u/new.py']) == 0.0, 'the own peak of a run never includes a prior'


def test_what_serve_admits_against_is_keyed_by_the_need_set_it_ran() -> None:
    """A box part is learned under (local, its need-set) and read back under the same key -- not under its OS.

    PLANTED CONTROL: the base part learned nothing, so it declares its prior -- the median of this host's measured
    parts (:func:`learned.part_prior`), never the whole box; a cluster reads slurm runs.
    """
    learned.record_run(REPO, learned.LOCAL, {'jmag'}, Run(peak_mem_gb=6.0, cpu_s=2 * 3600.0, wall_s=3600.0))
    jmag = learned.box_demand(REPO, frozenset({'jmag'}), cpu=8, mem_gb=64)
    assert jmag == Demand(cpu=2, mem_gb=6.0 * learned.BOX_HEADROOM, jmag=1)
    assert learned.box_demand(REPO, frozenset(), cpu=8, mem_gb=64) == Demand(cpu=2, mem_gb=6.0 * learned.BOX_HEADROOM)
    assert learned.cluster_demand(REPO)(frozenset({'jmag'})) == Demand(cpu=1, mem_gb=1.0, jmag=1, files=50_000)


def test_a_cluster_part_demands_the_memory_one_task_of_its_plan_asks() -> None:
    """Measured 2026-10-16: admitted at a 13 GB run peak, the part was SUBMITTED at 352 GB a task.

    The demand is :func:`learned.task_gb` of the peak -- the plan's own formula -- and a known larger ask
    (:func:`learned.record_ask`) raises it. PLANTED CONTROL: a smaller ask never lowers it.
    """
    learned.record_run(REPO, learned.SLURM, (), Run(peak_mem_gb=13.0, cpu_s=89875.0, wall_s=8616.0))
    ask = learned.cluster_demand(REPO)
    assert ask(frozenset()) == Demand(cpu=11, mem_gb=13.0 * 1.5, files=50_000)
    learned.record_ask(REPO, learned.SLURM, (), 352.2, safety=1.5)
    assert ask(frozenset())['mem_gb'] == pytest.approx(352.2, rel=1e-12, abs=0.0)
    learned.record_ask(REPO, learned.SLURM, (), 8.0, safety=1.5)
    assert ask(frozenset())['mem_gb'] == pytest.approx(352.2, rel=1e-12, abs=0.0)
    assert ask(frozenset())['cpu'] == 11, 'the ask moves memory only'


def test_a_box_part_declares_what_it_kept_busy_capped_at_the_box() -> None:
    """The demand a box part is ADMITTED at is the claim the box then ENFORCES, so it never exceeds the box.

    PLANTED CONTROL: a box already running a part declared at the whole box admits no second one.
    """
    learned.record_run(REPO, learned.LOCAL, {'femm'}, Run(peak_mem_gb=64.0, cpu_s=30 * 3600.0, wall_s=3600.0))
    learned.record_run(REPO, learned.LOCAL, {'jmag'}, Run(peak_mem_gb=60.0, cpu_s=64 * 3600.0, wall_s=3600.0))
    plain, jmag = Job('', 'a' * 40, 'windows'), Job('', 'b' * 40, 'windows', frozenset({'jmag'}))
    box = Supply('G', Resources(cpu=30, mem_gb=64.0, jmag=1))
    measured = learned.box_demand(REPO, frozenset({'jmag'}), cpu=30, mem_gb=64)
    assert measured == Demand(cpu=30, mem_gb=64.0, jmag=1), 'cores and memory capped at the box'
    assert admit(jmag, measured, box)
    whole = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert whole == Demand(cpu=30, mem_gb=64.0, femm=1)
    plain = Job('', 'a' * 40, 'windows', frozenset({'femm'}))
    box = Supply('G', Resources(cpu=30, mem_gb=64.0, jmag=1, femm=1))
    assert admit(plain, whole, box)
    assert admit(plain, whole, box, running=(Resources(whole),)).lacking == ('cpu', 'femm', 'mem_gb')


def test_a_request_is_learned_under_its_producer_name_never_a_test_parts_record() -> None:
    """A request shares no record with a test part of its need-set: unmeasured, it asks what its row declares."""
    jmag = frozenset({'jmag'})
    learned.record_run(REPO, learned.LOCAL, jmag, Run(peak_mem_gb=60.0, cpu_s=64 * 3600.0, wall_s=3600.0))
    declared = learned.request_demand(REPO, 'deck_solve', jmag, cpu=4, mem_gb=8.0)
    assert declared == Demand(cpu=4, mem_gb=8.0, jmag=1), 'the test part measured on the same need-set is not it'
    learned.record_run(REPO, 'deck_solve', jmag, Run(peak_mem_gb=10.0, cpu_s=2 * 3600.0, wall_s=3600.0))
    measured = learned.request_demand(REPO, 'deck_solve', jmag, cpu=4, mem_gb=8.0)
    assert measured == Demand(cpu=2, mem_gb=10.0 * learned.BOX_HEADROOM, jmag=1), 'measured beats declared'


def test_an_unmeasured_part_is_priced_by_the_d5_prior_never_the_whole_box() -> None:
    """D5: the host's measured parts first, else every host's forge records for its need-set, else the task floor."""
    floor = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert floor == Demand(cpu=1, mem_gb=1.0 * learned.BOX_HEADROOM, femm=1), 'nothing known: the per-task floor'
    learned.learn_record(REPO, 'r1.json.gz', {'repo': REPO, 'platform': 'femm', 'peaks_mb': {'t/a.py': 2048.0}})
    learned.learn_record(REPO, 'r2.json.gz', {'repo': REPO, 'platform': 'femm', 'peaks_mb': {'t/a.py': 6144.0}})
    learned.learn_record(REPO, 'r3.json.gz', {'repo': REPO, 'platform': 'base', 'peaks_mb': {'t/a.py': 99999.0}})
    forged = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert forged == Demand(cpu=1, mem_gb=4.0 * learned.BOX_HEADROOM, femm=1), 'the median of its need-set only'
    learned.record_run(REPO, learned.LOCAL, {'jmag'}, Run(peak_mem_gb=8.0, cpu_s=4 * 3600.0, wall_s=3600.0))
    learned.record_run(REPO, learned.LOCAL, (), Run(peak_mem_gb=2.0, cpu_s=2 * 3600.0, wall_s=3600.0))
    local = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert local == Demand(cpu=3, mem_gb=5.0 * learned.BOX_HEADROOM, femm=1), "this host's parts come first"
    learned.record_run(REPO, learned.LOCAL, {'femm'}, Run(peak_mem_gb=20.0, cpu_s=6 * 3600.0, wall_s=3600.0))
    ran = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert ran == Demand(cpu=6, mem_gb=20.0 * learned.BOX_HEADROOM, femm=1), 'once run, its own peak replaces the prior'


def test_a_part_priced_at_the_whole_box_is_refused_on_a_busy_box_and_its_prior_is_admitted() -> None:
    """THE ALWAYS-REFUSED PART: a busy box's measured room is under its size, so a whole-box ask never fits.

    PLANTED CONTROL: the whole-box ask is refused where the D5 prior of the same part is admitted.
    """
    learned.record_run(REPO, learned.LOCAL, (), Run(peak_mem_gb=4.0, cpu_s=2 * 3600.0, wall_s=3600.0))
    part = Job('', 'a' * 40, 'windows', frozenset({'femm'}))
    busy = Supply('G', Resources(cpu=12, mem_gb=20.0, femm=1))
    whole = Demand(cpu=30, mem_gb=64.0, femm=1)
    assert not admit(part, whole, busy)
    prior = learned.box_demand(REPO, frozenset({'femm'}), cpu=30, mem_gb=64)
    assert prior == Demand(cpu=2, mem_gb=4.0 * learned.BOX_HEADROOM, femm=1)
    assert admit(part, prior, busy)
