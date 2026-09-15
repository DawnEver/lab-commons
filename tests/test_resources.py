"""``lab_commons.resources`` — the box-resource broker.

The GENERIC half of motronics-studio's ``docs-src/dev/compute-resources.md`` design. Nothing here
knows what a vendor is; a consumer declares the pools and the values.

Every guard below PLANTS its exhaustion condition and calls the REAL guard, injecting the readers
(``read_memory=``, ``read_working_set=``) rather than mocking the guard itself. The slot root is
redirected per test, because "box-global" is the property under test and cannot be mocked away
without testing nothing.
"""

import os
import tempfile
import threading
import time

import pytest

from lab_commons.resources import (
    CONSERVATIVE_MEMORY_SHARE,
    CPU,
    DIMENSIONS,
    DISK,
    GPU,
    MEMORY,
    SEATS,
    WALLCLOCK,
    Accounting,
    Basis,
    Broker,
    Capacity,
    CapacityRegistry,
    CeilingExceeded,
    Dimension,
    Enforcement,
    Exhausted,
    JobHandle,
    MemoryUnreadable,
    SystemMemory,
    _check_dimension_table,
)

GIB = 1024**3


@pytest.fixture
def registry():
    return CapacityRegistry()


@pytest.fixture
def broker(registry, tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
    return Broker(registry, read_memory=lambda: SystemMemory(total_bytes=64 * GIB, available_bytes=32 * GIB))


def roomy(total=64 * GIB, available=32 * GIB):
    return lambda: SystemMemory(total_bytes=total, available_bytes=available)


def seat(pool: str, index: int = 0) -> str:
    """The record file one indexed seat of *pool* lives in, by the broker's own naming rule.

    Written out rather than imported: the LAYOUT is the shared medium between processes on
    different revisions of this package, so a test that plants a holder must plant it where a peer
    -- not merely this build -- would look for it.
    """
    return f'{pool}.{SEATS.name}.{index}.slot'


class TestDimensionsAreData:
    """Axis 1 — resource dimensions are registry data, not branches on a kind."""

    def test_the_four_dimensions_with_values_are_declared(self):
        assert {SEATS.name, MEMORY.name, CPU.name, WALLCLOCK.name} <= set(DIMENSIONS)

    def test_disk_and_gpu_have_the_shape_without_values(self, registry):
        # Declared so a consumer can add a value without a code change here; UNVALUED so nothing
        # silently believes this box was measured for them.
        for dimension in (DISK, GPU):
            assert dimension.name in DIMENSIONS
            assert registry.capacity('anything', dimension.name).basis is Basis.CONSERVATIVE_DEFAULT

    def test_every_dimension_carries_its_unit(self):
        assert MEMORY.unit == 'bytes'
        assert SEATS.unit == 'count'
        assert CPU.unit == 'cores'
        assert WALLCLOCK.unit == 'seconds'

    def test_a_demand_in_an_unknown_dimension_raises(self, broker, registry):
        registry.declare('pool', Capacity.measured(SEATS.name, 2, on=broker.hostname))
        with pytest.raises(KeyError, match='unicorns'), broker.admit('pool', {'unicorns': 1}):
            pass


class TestMeasurementVersusStructuralConstant:
    """Axis 4 — a per-box measurement and an everywhere-identical fact are different KINDS."""

    def test_a_measurement_must_name_the_box_it_was_measured_on(self):
        with pytest.raises(ValueError, match='measured_on'):
            Capacity(dimension=SEATS.name, value=4, basis=Basis.MEASURED, measured_on=None)

    def test_a_structural_constant_must_not_name_a_box(self):
        # Declaring an everywhere-identical fact per-box is what makes it RAISE on a machine
        # nobody remembered to declare.
        with pytest.raises(ValueError, match='every box'):
            Capacity(dimension=SEATS.name, value=1, basis=Basis.STRUCTURAL, measured_on='thisbox')

    def test_a_structural_constant_holds_on_any_hostname(self, registry):
        registry.declare('attaching-tool', Capacity.structural(SEATS.name, 1, note='COM Dispatch attaches'))
        for hostname in ('boxA', 'boxB'):
            found = registry.capacity('attaching-tool', SEATS.name, hostname=hostname)
            assert found.value == 1
            assert found.basis is Basis.STRUCTURAL

    def test_a_measurement_copied_to_another_box_is_refused_not_believed(self, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 8, on='the-big-box'))
        found = registry.capacity('tool', SEATS.name, hostname='some-laptop')
        assert found.basis is Basis.CONSERVATIVE_DEFAULT
        assert found.value == 1, 'a copied limit must fall to the conservative value, not be trusted'
        assert 'the-big-box' in found.note

    def test_a_structural_constant_wins_over_a_larger_measurement(self, registry):
        # Both kinds are ceilings; the binding one is the SMALLER. A box measured at 4 seats for a
        # tool whose sessions are structurally exclusive still gets 1.
        registry.declare('tool', Capacity.measured(SEATS.name, 4, on='thisbox'))
        registry.declare('tool', Capacity.structural(SEATS.name, 1, note='sessions are exclusive'))
        assert registry.capacity('tool', SEATS.name, hostname='thisbox').value == 1


class TestAnUnmeasuredBoxIsConservativeAndVisible:
    """Decision 2 — a conservative default plus FORCED VISIBILITY, never a refusal and never a
    permissive guess. Visibility travels in the RETURN VALUE: a warning on an unchanged success
    return is the forbidden shape, and the test is whether the CALLER can tell."""

    def test_an_unmeasured_pool_is_admitted_not_refused(self, broker):
        with broker.admit('never-measured', {SEATS.name: 1}) as grant:
            assert grant.pool == 'never-measured'

    def test_an_unmeasured_pool_serialises_to_one_seat(self, broker):
        # "Conservative" is the direction that CANNOT crash the box.
        with (
            broker.admit('never-measured', {SEATS.name: 1}),
            pytest.raises(Exhausted),
            broker.admit('never-measured', {SEATS.name: 1}),
        ):
            pass

    def test_an_unmeasured_memory_floor_does_not_disappear(self, registry, tmp_path, monkeypatch):
        # The anti-pattern being refused: falling back to cpu-only so "the memory constraint
        # disappears". Unmeasured means a CONSERVATIVE floor -- a share of TOTAL -- not no floor.
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        tight = Broker(registry, read_memory=roomy(total=64 * GIB, available=int(0.1 * 64 * GIB)))
        with pytest.raises(Exhausted) as excinfo, tight.admit('never-measured', {MEMORY.name: 1 * GIB}):
            pass
        assert excinfo.value.dimension == MEMORY.name
        assert int(CONSERVATIVE_MEMORY_SHARE * 64 * GIB) == excinfo.value.needed

    def test_the_caller_can_tell_the_grant_rested_on_a_default(self, broker):
        with broker.admit('never-measured', {SEATS.name: 1}) as grant:
            assert grant.is_fully_declared is False
            assert grant.conservative == (SEATS.name,)
            assert grant.basis[SEATS.name] is Basis.CONSERVATIVE_DEFAULT

    def test_a_measured_grant_says_so(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 4, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}) as grant:
            assert grant.is_fully_declared is True
            assert grant.conservative == ()

    def test_the_admission_explains_itself_in_one_line(self, broker):
        with broker.admit('never-measured', {SEATS.name: 1}) as grant:
            explained = grant.explain()
        assert 'never-measured' in explained
        assert 'conservative' in explained.lower()


class TestSeatsAreEnforcedAcrossProcesses:
    def test_two_holders_fit_under_a_limit_of_two(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 2, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}), broker.admit('tool', {SEATS.name: 1}):
            assert len(broker.holders('tool')) == 2

    def test_the_third_is_refused_and_the_refusal_names_the_holders(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 2, on=broker.hostname))
        with (
            broker.admit('tool', {SEATS.name: 1}, what='solve-A'),
            broker.admit('tool', {SEATS.name: 1}),
            pytest.raises(Exhausted) as excinfo,
            broker.admit('tool', {SEATS.name: 1}),
        ):
            pass
        assert 'solve-A' in str(excinfo.value)
        assert excinfo.value.dimension == SEATS.name

    def test_a_released_seat_is_free_again(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}):
            pass
        with broker.admit('tool', {SEATS.name: 1}):
            assert len(broker.holders('tool')) == 1

    def test_a_record_left_by_a_dead_holder_is_free_not_held(self, broker, registry, tmp_path):
        # Staleness is asked of the OS, never of a clock: the worst outcome of any crash is a
        # file nobody counts, never a box that refuses everything until a human cleans up.
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        broker.resource_dir().joinpath(seat('tool')).write_text(
            '{"job_kind": "pid", "job_ident": "424242", "what": "a corpse", "pool": "tool", "demands": {}}',
            encoding='utf-8',
        )
        with broker.admit('tool', {SEATS.name: 1}) as grant:
            assert grant.pool == 'tool'

    def test_the_pools_do_not_contend_with_each_other(self, broker, registry):
        registry.declare('a', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        registry.declare('b', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        with broker.admit('a', {SEATS.name: 1}), broker.admit('b', {SEATS.name: 1}):
            assert len(broker.holders('a')) == 1


class TestTheReservationTracksTheJobNotTheClient:
    """Defect 1b — the crash mechanism. The slot recorded ``os.getpid()``, the PYTHON CLIENT's pid,
    so a vendor process outliving its driver held ZERO seats and nothing believed it was still
    eating the box."""

    def test_a_fresh_grant_is_held_by_this_client(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}) as grant:
            assert grant.job == JobHandle.for_client()
            assert grant.job.ident == str(os.getpid())

    def test_a_grant_can_be_re_pointed_at_the_job_it_started(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            assert broker.holders('tool')[0].job.kind == 'pid'

    def test_a_seat_held_by_a_live_job_survives_its_dead_client(self, broker, registry):
        # The measured incident, reconstructed: the client gave up, its `finally` released the
        # seat, and the vendor process ran on for an hour with six live processes in the census.
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        broker.resource_dir().joinpath(seat('tool')).write_text(
            f'{{"job_kind": "pid", "job_ident": "{os.getpid()}", "what": "an orphaned solve", '
            f'"pool": "tool", "demands": {{}}}}',
            encoding='utf-8',
        )
        with pytest.raises(Exhausted, match='an orphaned solve'), broker.admit('tool', {SEATS.name: 1}):
            pass

    def test_an_opaque_job_handle_is_resolved_by_the_consumer_not_by_us(self, registry, tmp_path, monkeypatch):
        # lab_commons must not learn what a scheduler is; it learns how to ASK.
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        asked: list[JobHandle] = []

        def liveness(job):
            asked.append(job)
            return True

        registry.declare('tool', Capacity.measured(SEATS.name, 1, on='thisbox'))
        alive = Broker(registry, read_memory=roomy(), liveness=liveness, hostname='thisbox')
        alive.resource_dir().joinpath(seat('tool')).write_text(
            '{"job_kind": "queue", "job_ident": "job-1774", "what": "a queued deck", "pool": "tool", "demands": {}}',
            encoding='utf-8',
        )
        with pytest.raises(Exhausted), alive.admit('tool', {SEATS.name: 1}):
            pass
        assert asked and asked[0] == JobHandle('queue', 'job-1774')

    def test_an_unresolvable_job_is_assumed_HELD_not_free(self, registry, tmp_path, monkeypatch):
        # The conservative direction again: "I cannot tell" must not free a seat, because freeing
        # one wrongly is the over-subscription this exists to prevent.
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on='thisbox'))
        unknown = Broker(registry, read_memory=roomy(), liveness=lambda _job: None, hostname='thisbox')
        unknown.resource_dir().joinpath(seat('tool')).write_text(
            '{"job_kind": "queue", "job_ident": "job-1", "what": "unknowable", "pool": "tool", "demands": {}}',
            encoding='utf-8',
        )
        with pytest.raises(Exhausted), unknown.admit('tool', {SEATS.name: 1}):
            pass


class TestMemoryAdmission:
    def test_a_declared_cost_that_fits_is_admitted(self, broker, registry):
        registry.declare('tool', Capacity.measured(MEMORY.name, 24 * GIB, on=broker.hostname))
        with broker.admit('tool', {MEMORY.name: 8 * GIB}) as grant:
            assert grant.basis[MEMORY.name] is Basis.MEASURED

    def test_a_declared_cost_over_this_box_free_ram_is_refused(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        tight = Broker(registry, read_memory=roomy(available=2 * GIB), hostname='thisbox')
        tight.registry.declare('tool', Capacity.measured(MEMORY.name, 60 * GIB, on='thisbox'))
        with pytest.raises(Exhausted) as excinfo, tight.admit('tool', {MEMORY.name: 16 * GIB}, what='a big deck'):
            pass
        assert excinfo.value.needed == 16 * GIB
        assert 'a big deck' in str(excinfo.value)

    def test_a_peer_claim_is_subtracted_before_the_comparison(self, broker, registry):
        # Without the subtraction this is check-then-start: N processes read the same free bytes,
        # all decide there is room, and all start. A peer admitted one second ago has allocated
        # almost nothing yet, so its INTENTION is what the next waiter must count.
        registry.declare('tool', Capacity.measured(SEATS.name, 4, on=broker.hostname))
        registry.declare('tool', Capacity.measured(MEMORY.name, 60 * GIB, on=broker.hostname))
        with (
            broker.admit('tool', {MEMORY.name: 20 * GIB, SEATS.name: 1}),
            pytest.raises(Exhausted) as excinfo,
            broker.admit('tool', {MEMORY.name: 20 * GIB, SEATS.name: 1}),
        ):
            pass
        assert excinfo.value.dimension == MEMORY.name

    def test_an_unreadable_box_is_not_an_unconstrained_one(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        blind = Broker(registry, read_memory=lambda: None, hostname='thisbox')
        with pytest.raises(MemoryUnreadable), blind.admit('tool', {MEMORY.name: 1 * GIB}):
            pass

    def test_the_wait_is_bounded_and_refuses_rather_than_hanging(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        tight = Broker(registry, read_memory=roomy(available=1 * GIB), hostname='thisbox')
        started = time.monotonic()
        with (
            pytest.raises(Exhausted) as excinfo,
            tight.admit('tool', {MEMORY.name: 16 * GIB}, wait_s=0.3, poll_s=0.05),
        ):
            pass
        assert 0.25 <= time.monotonic() - started < 5.0
        assert excinfo.value.waited_s >= 0.25

    def test_a_wait_that_the_box_satisfies_is_admitted(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
        readings = iter([SystemMemory(64 * GIB, 1 * GIB), SystemMemory(64 * GIB, 40 * GIB)])
        freeing = Broker(registry, read_memory=lambda: next(readings, SystemMemory(64 * GIB, 40 * GIB)))
        with freeing.admit('tool', {MEMORY.name: 16 * GIB}, wait_s=2.0, poll_s=0.01) as grant:
            assert grant.waited_s > 0


class TestObservingARunningJob:
    """Defect 1 — admission alone only POSTPONES a crash. Nothing sampled a live job's working
    set and acted on it."""

    def test_a_grant_samples_the_job_it_tracks(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}, read_working_set=lambda _pid: 7 * GIB) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            assert grant.observe().working_set_bytes == 7 * GIB

    def test_a_job_with_no_pid_cannot_be_sampled(self, broker):
        with broker.admit('tool', {SEATS.name: 1}) as grant:
            grant.track(JobHandle('queue', 'job-1'))
            assert grant.observe() is None

    def test_the_peak_is_kept_so_a_declaration_can_be_checked(self, broker):
        # "A declaration that lies is the dominant defect" -- so the broker MEASURES the peak and
        # compares it to the estimate. Systematic under-declaration becomes a recorded RATIO
        # rather than a box crash.
        readings = iter([2 * GIB, 9 * GIB, 3 * GIB])
        with broker.admit('tool', {MEMORY.name: 4 * GIB}, read_working_set=lambda _pid: next(readings)) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            for _ in range(3):
                grant.observe()
            assert grant.peak_bytes == 9 * GIB
            assert grant.declared_ratio(MEMORY.name) == pytest.approx(9 / 4)

    def test_an_unobserved_job_has_no_ratio_rather_than_a_flattering_one(self, broker):
        with broker.admit('tool', {MEMORY.name: 4 * GIB}) as grant:
            assert grant.peak_bytes is None
            assert grant.declared_ratio(MEMORY.name) is None


class TestTheRunningCeiling:
    """Axis 3's new half. One constraint is absolute: NEVER evict another party's run."""

    def test_a_job_over_its_own_ceiling_is_ended(self, broker):
        ended: list[int] = []
        with broker.admit('tool', {MEMORY.name: 1 * GIB}, read_working_set=lambda _pid: 9 * GIB) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(ceiling_bytes=4 * GIB, on_breach=ended.append, interval_s=0.01)
            watch.join(timeout=5.0)
        assert ended == [os.getpid()]
        assert isinstance(watch.breach, CeilingExceeded)
        assert watch.breach.held_bytes == 9 * GIB

    def test_a_compliant_job_is_left_alone(self, broker):
        ended: list[int] = []
        with broker.admit('tool', {MEMORY.name: 8 * GIB}, read_working_set=lambda _pid: 1 * GIB) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(ceiling_bytes=4 * GIB, on_breach=ended.append, interval_s=0.01)
            time.sleep(0.1)
            watch.stop()
            watch.join(timeout=5.0)
        assert ended == []
        assert watch.breach is None

    def test_an_unreadable_working_set_is_not_a_breach(self, broker):
        # A process that has EXITED reads the same as one we cannot query, so the watch simply
        # ends -- killing on an unreadable sample would evict a job that had already finished.
        ended: list[int] = []
        with broker.admit('tool', {MEMORY.name: 1 * GIB}, read_working_set=lambda _pid: None) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(ceiling_bytes=1, on_breach=ended.append, interval_s=0.01)
            watch.join(timeout=5.0)
        assert ended == []
        assert watch.breach is None

    def test_the_ceiling_defaults_to_the_declared_cost_and_never_to_no_ceiling(self, broker):
        ended: list[int] = []
        with broker.admit('tool', {MEMORY.name: 1 * GIB}, read_working_set=lambda _pid: 3 * GIB) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(on_breach=ended.append, interval_s=0.01)
            watch.join(timeout=5.0)
        assert ended == [os.getpid()], 'a job with no explicit ceiling is bounded by what it DECLARED'

    def test_a_job_we_do_not_own_can_never_be_bounded(self, broker, registry):
        # A verdict in progress is someone's evidence. Ownership is enforced by the CODE: the only
        # route to a watch is through a grant, and a grant may only bound the job it tracks.
        registry.declare('tool', Capacity.measured(SEATS.name, 2, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1}) as mine, broker.admit('tool', {SEATS.name: 1}) as theirs:
            theirs.track(JobHandle.for_pid(4242))
            mine.track(JobHandle.for_pid(os.getpid()))
            with pytest.raises(PermissionError, match='never evict'):
                mine.enforce_ceiling(job=theirs.job, ceiling_bytes=1)

    def test_a_grant_tracking_nothing_bounds_nothing(self, broker):
        with broker.admit('tool', {MEMORY.name: 1 * GIB}) as grant:
            grant.track(JobHandle('queue', 'job-9'))
            with pytest.raises(ValueError, match='no pid'):
                grant.enforce_ceiling(ceiling_bytes=1)

    def test_the_watch_thread_is_a_daemon_so_it_cannot_outlive_its_caller(self, broker):
        with broker.admit('tool', {MEMORY.name: 1 * GIB}, read_working_set=lambda _pid: 1) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(ceiling_bytes=8 * GIB, on_breach=lambda _pid: None, interval_s=0.01)
            assert isinstance(watch.thread, threading.Thread)
            assert watch.thread.daemon is True
            watch.stop()

    def test_leaving_the_grant_stops_the_watch(self, broker):
        # A watchdog needing a matching close() is a watchdog that stops running the first time a
        # caller raises.
        with broker.admit('tool', {MEMORY.name: 8 * GIB}, read_working_set=lambda _pid: 1) as grant:
            grant.track(JobHandle.for_pid(os.getpid()))
            watch = grant.enforce_ceiling(ceiling_bytes=8 * GIB, on_breach=lambda _pid: None, interval_s=0.01)
        watch.thread.join(timeout=5.0)
        assert watch.thread.is_alive() is False


class TestTheRecordDoesNotDependOnTheCallerRevision:
    def test_the_resource_root_is_outside_any_repository(self, registry, monkeypatch):
        # Per-box runtime state naming a pid, shared by every worktree on the box REGARDLESS of
        # which revision each has checked out. The tree is not where machine state goes.
        monkeypatch.delenv('LAB_COMMONS_RESOURCE_DIR', raising=False)
        assert str(Broker(registry).resource_dir()).startswith(tempfile.gettempdir())

    def test_a_record_written_by_an_unknown_future_version_is_still_counted(self, broker, registry):
        # Forward compatibility is the whole point of a box-global shared medium: a holder written
        # by a NEWER broker must not be read as a free seat by an older one.
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        broker.resource_dir().joinpath(seat('tool')).write_text(
            f'{{"job_kind": "pid", "job_ident": "{os.getpid()}", "what": "x", "pool": "tool", '
            f'"demands": {{}}, "from_2030": true}}',
            encoding='utf-8',
        )
        with pytest.raises(Exhausted), broker.admit('tool', {SEATS.name: 1}):
            pass

    def test_an_unparseable_record_is_HELD_rather_than_stolen_or_crashed_on(self, broker, registry):
        """A record that exists and cannot be read is a HOLDER, and the guard REFUSES on it.

        The condition is PLANTED, and it is not a contrived one: a seat file with no readable
        record is exactly what the acquisition path used to leave behind for an instant, and what a
        peer found there was a free index to delete and take (measured 2026-09-15 -- two holders on
        a one-seat pool). Reading it as ABSENT is the defect; the old subject -- admission must not
        CRASH on a stray file -- survives as the refusal being the broker's own, not a ValueError
        out of ``json.loads``.
        """
        registry.declare('tool', Capacity.measured(SEATS.name, 1, on=broker.hostname))
        planted = broker.resource_dir() / seat('tool')
        planted.write_text('not json at all', encoding='utf-8')
        with pytest.raises(Exhausted) as refusal, broker.admit('tool', {SEATS.name: 1}):
            pass
        assert refusal.value.dimension == SEATS.name
        assert planted.exists(), 'the unreadable seat was DELETED -- that is the theft, not a fix'


class TestADimensionNobodyEnforcesSaysSo:
    """The arm a type checker pointed at, and the defect underneath it.

    Pyright flagged three sites where an ``int | None`` reached ``min(key=)`` or ``int(...)``,
    because DISK and GPU are declared with ``conservative=None``. The crash it predicted is NOT
    reachable -- ``TestTheValuelessCapacityArmsAreNarrowed`` below plants each arm and shows why,
    rather than arguing it. But driving those dimensions through the REAL ``admit()`` to find that
    out exposed something worse: a demand of 99 CORES was admitted on a box whose conservative cpu
    ceiling is 1, and the grant reported ``conservative == ('cpu', ...)`` -- which READS as
    "bounded at the value that cannot crash the box" and was in fact "not bounded at all".

    ``conservative`` was itself a declaration that lies: it could not distinguish a ceiling held
    AT the conservative value from a dimension nothing ever looked at.
    """

    def test_a_disk_demand_is_admitted_rather_than_crashing(self, broker):
        with broker.admit('tool', {DISK.name: 10 * GIB}) as grant:
            assert grant.pool == 'tool'

    def test_a_disk_demand_is_reported_unbounded(self, broker):
        with broker.admit('tool', {DISK.name: 10 * GIB}) as grant:
            assert DISK.name in grant.unbounded
            assert grant.is_fully_declared is False

    def test_a_gpu_demand_is_reported_unbounded(self, broker):
        with broker.admit('tool', {GPU.name: 2}) as grant:
            assert GPU.name in grant.unbounded

    def test_wallclock_is_recorded_and_reported_unbounded(self, broker):
        # It is a DURATION, not a stock: it is never summed across holders, so admission cannot
        # bound it and must not imply that it did.
        with broker.admit('tool', {WALLCLOCK.name: 3600}) as grant:
            assert WALLCLOCK.name in grant.unbounded
            assert grant.demands[WALLCLOCK.name] == 3600

    def test_a_bounded_dimension_is_not_reported_unbounded(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 2, on=broker.hostname))
        with broker.admit('tool', {SEATS.name: 1, MEMORY.name: 1 * GIB}) as grant:
            assert grant.unbounded == ()

    def test_unbounded_is_not_the_same_fact_as_conservative(self, broker):
        # The distinction the old `conservative` could not draw: seats IS enforced, at the
        # conservative value; disk is not enforced at all.
        with broker.admit('tool', {DISK.name: 1, SEATS.name: 1}) as grant:
            assert SEATS.name in grant.conservative
            assert SEATS.name not in grant.unbounded
            assert DISK.name in grant.unbounded

    def test_the_explanation_names_what_nothing_bounded(self, broker):
        with broker.admit('tool', {DISK.name: 1}) as grant:
            assert 'unbounded' in grant.explain().lower()
            assert DISK.name in grant.explain()

    def test_a_declared_disk_value_is_STILL_unbounded_because_nothing_can_read_it(self, broker, registry):
        # A number nobody can check is not a ceiling. Letting a declared value flip `unbounded`
        # off would be the same lie one level up -- the registry would be claiming an enforcement
        # the code does not have.
        registry.declare('tool', Capacity.measured(DISK.name, 500 * GIB, on=broker.hostname))
        with broker.admit('tool', {DISK.name: 10 * GIB}) as grant:
            assert DISK.name in grant.unbounded

    def test_the_table_guard_refuses_an_enforced_dimension_with_no_conservative_value(self):
        # Planted on a table of its own, so the guard is DRIVEN rather than merely running at
        # import over a table that happens to be correct.
        planted = Dimension('seats', 'count', Accounting.COUNTED, Enforcement.RECORDS, conservative=None)
        with pytest.raises(ValueError, match='conservative'):
            _check_dimension_table([planted])

    def test_the_table_guard_refuses_a_value_on_a_dimension_nothing_enforces(self):
        # The other side of the ratchet: a value in the table reads as a ceiling that is applied.
        planted = Dimension('disk', 'bytes', Accounting.BOX_GLOBAL, Enforcement.NONE, conservative=4)
        with pytest.raises(ValueError, match='enforced by nothing'):
            _check_dimension_table([planted])

    def test_the_real_table_passes_its_own_guard(self):
        _check_dimension_table(DIMENSIONS.values())

    def test_every_enforced_dimension_declares_a_conservative_value(self):
        # The floor under the scan: without it this would be vacuous, and it is what makes the
        # seat-limit narrowing below UNREACHABLE rather than merely untested.
        enforced = [dimension for dimension in DIMENSIONS.values() if dimension.enforcement is not Enforcement.NONE]
        assert enforced, 'no dimension is enforced at all -- the broker would bound nothing'
        for dimension in enforced:
            assert isinstance(dimension.conservative, int), f'{dimension.name} is enforced with no conservative value'


class TestCoresAreEnforcedLikeSeats:
    """CPU was declared a COUNTED dimension while nothing counted it."""

    def test_cores_aggregate_across_live_holders(self, broker, registry):
        registry.declare('tool', Capacity.measured(SEATS.name, 8, on=broker.hostname))
        registry.declare('tool', Capacity.measured(CPU.name, 4, on=broker.hostname))
        with (
            broker.admit('tool', {CPU.name: 2}, what='wide-A'),
            broker.admit('tool', {CPU.name: 2}),
            pytest.raises(Exhausted) as excinfo,
            broker.admit('tool', {CPU.name: 2}),
        ):
            pass
        assert excinfo.value.dimension == CPU.name
        assert 'wide-A' in str(excinfo.value)

    def test_a_job_wider_than_this_box_is_refused_outright(self, broker, registry):
        registry.declare('tool', Capacity.measured(CPU.name, 4, on=broker.hostname))
        with pytest.raises(Exhausted) as excinfo, broker.admit('tool', {CPU.name: 16}):
            pass
        assert excinfo.value.dimension == CPU.name

    def test_an_unmeasured_box_grants_the_minimum_width_and_refuses_a_wide_job(self, broker):
        # The measured defect: `{cpu: 99}` was ADMITTED on a box declaring nothing, while the
        # grant reported cpu as "conservative".
        with pytest.raises(Exhausted) as excinfo, broker.admit('never-measured', {CPU.name: 99}):
            pass
        assert excinfo.value.dimension == CPU.name
        assert excinfo.value.needed == 99

    def test_cores_are_released_with_the_grant(self, broker, registry):
        registry.declare('tool', Capacity.measured(CPU.name, 4, on=broker.hostname))
        with broker.admit('tool', {CPU.name: 4}):
            pass
        with broker.admit('tool', {CPU.name: 4}) as grant:
            assert grant.basis[CPU.name] is Basis.MEASURED


class TestTheValuelessCapacityArmsAreNarrowed:
    """The three sites a type checker flagged. Each is PLANTED here, so "unreachable" is a
    measurement rather than a claim -- and the code is narrowed so the type says it too."""

    def test_a_valueless_declaration_never_reaches_the_compose_minimum(self, registry):
        # `min(..., key=lambda c: c.value)` over a None would raise TypeError. It cannot: the
        # filter drops valueless candidates BEFORE the minimum.
        registry.declare('tool', Capacity(dimension=SEATS.name, value=None, basis=Basis.CONSERVATIVE_DEFAULT))
        registry.declare('tool', Capacity.measured(SEATS.name, 4, on='thisbox'))
        assert registry.capacity('tool', SEATS.name, hostname='thisbox').value == 4

    def test_only_valueless_declarations_fall_to_the_conservative_default(self, registry):
        registry.declare('tool', Capacity(dimension=SEATS.name, value=None, basis=Basis.CONSERVATIVE_DEFAULT))
        found = registry.capacity('tool', SEATS.name, hostname='thisbox')
        assert found.basis is Basis.CONSERVATIVE_DEFAULT
        assert found.value == 1

    def test_a_declared_limit_of_zero_refuses_every_job(self, broker, registry):
        # Why the narrowing is an explicit `is None` and not `value or FLOOR`: zero is a MEANINGFUL
        # declaration -- "this box may not run this at all" -- and `or` would silently promote it
        # to one, which is the permissive direction.
        registry.declare('tool', Capacity.measured(SEATS.name, 0, on=broker.hostname))
        with pytest.raises(Exhausted) as excinfo, broker.admit('tool', {SEATS.name: 1}):
            pass
        assert excinfo.value.dimension == SEATS.name
