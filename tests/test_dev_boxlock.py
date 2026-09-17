"""``lab_commons.dev.boxlock`` — one CPU-saturating run at a time, refusing by name.

The lock is the broker that already ships in this package, so what is tested here is the POLICY:
that a BOX-scoped seat is the exclusion whoever's pool name the run carries, that a per-box seat
measurement cannot lift it, and that a refusal arrives as the broker's own ``Exhausted`` -- carrying
the holder's name -- rather than as a second exception class that a caller would have to learn.

The slot root is redirected per test, because "box-wide" is the property under test and cannot be
mocked away without testing nothing.
"""

from pathlib import Path

import pytest

from lab_commons.dev.boxlock import BOX_POOL, BoxLock
from lab_commons.resources import (
    BOX_SEATS,
    MEMORY,
    SEATS,
    Basis,
    Broker,
    Capacity,
    CapacityRegistry,
    Exhausted,
    SystemMemory,
)

GIB = 1024**3

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def slots(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
    return tmp_path / 'slots'


def seat(pool: str, index: int = 0) -> str:
    """A POOL-scoped seat file's name, written out rather than imported.

    The layout is the shared medium between processes on different revisions of this package, so a
    test that plants a holder plants it where a peer -- not merely this build -- would look.
    """
    return f'{pool}.{SEATS.name}.{index}.slot'


def box_seat(index: int = 0) -> str:
    """The BOX-scoped seat file's name.

    It carries NO pool, and that absence is the mechanism: a name with a pool in it is exactly the
    string a consumer could have opted out of.
    """
    return f'{BOX_SEATS.name}.{index}.slot'


def _take_a_second_seat(what: str, broker: Broker | None = None) -> None:
    """Attempt to hold the box while somebody else already does. A helper, not a nested ``with``.

    Written as a call because the assertion lives in the OUTER block: flattening the two contexts
    into one ``with`` would put the ``pytest.raises`` around the very statements that must not
    raise.
    """
    with BoxLock(what, broker=broker).held():
        pytest.fail(f'{what} was admitted while the box was already held')


class TestTheExclusion:
    def test_a_second_holder_is_refused_and_the_refusal_names_the_first(self, slots) -> None:
        """THE DECISIVE ONE.

        "Busy" sends its reader to the process table to guess, and guessing wrong kills somebody's
        evidence.
        """
        with BoxLock('gate:integrate/main').held(), pytest.raises(Exhausted) as refusal:
            _take_a_second_seat('heavy:feat/structural')
        assert 'gate:integrate/main' in str(refusal.value)
        assert refusal.value.pool == BOX_POOL

    def test_the_seat_is_free_again_once_the_holder_releases_it(self, slots) -> None:
        with BoxLock('first').held():
            pass
        with BoxLock('second').held() as grant:
            assert grant.pool == BOX_POOL

    def test_a_dead_holders_record_is_free_not_held(self, slots) -> None:
        """The worst outcome of any crash is a file nobody counts, never a box nobody can use."""
        dead = slots / seat(BOX_POOL)
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text('{"job_kind": "pid", "job_ident": "999999999", "demands": {}}', encoding='utf-8')
        with BoxLock('after a crash').held():
            pass

    def test_a_holder_with_no_name_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match='cannot be acted on'):
            BoxLock('   ')


class TestWhatTheLockDeclares:
    def test_the_seats_rest_on_structural_constants_not_on_a_box_measurement(self, slots) -> None:
        """The rule is a property of the TOOL -- one at a time, everywhere -- not of this machine."""
        with BoxLock('gate').held() as grant:
            assert grant.basis[BOX_SEATS.name] is Basis.STRUCTURAL
            assert grant.capacity[BOX_SEATS.name].value == 1
            assert grant.basis[SEATS.name] is Basis.STRUCTURAL
            assert grant.capacity[SEATS.name].value == 1

    def test_a_per_box_measurement_of_four_seats_cannot_lift_it(self, slots) -> None:
        """Declarations compose by taking the SMALLEST, so the structural constant always wins."""
        registry = CapacityRegistry()
        registry.declare(BOX_POOL, Capacity.measured(SEATS.name, 4, on=Broker().hostname))
        broker = Broker(registry)
        with BoxLock('gate', broker=broker).held() as grant:
            conceded = grant.capacity[SEATS.name].value
            with pytest.raises(Exhausted):
                _take_a_second_seat('heavy', broker)
        assert conceded == 1

    def test_a_larger_measurement_of_the_box_seat_under_ANOTHER_POOL_cannot_lift_it(self, slots) -> None:
        """A box-scoped ceiling is declared FOR THE BOX, so no pool keeps one to itself.

        This is the declaration half of the scope: with the ceiling keyed by pool, a consumer could
        have raised the box seat by declaring it under a name of its own -- the same opt-out the
        shared pool string allowed, one level down.
        """
        registry = CapacityRegistry()
        registry.declare('some-other-pool', Capacity.measured(BOX_SEATS.name, 8, on=Broker().hostname))
        registry.declare('some-other-pool', Capacity.structural(BOX_SEATS.name, 1, note='one at a time'))
        with BoxLock('gate', broker=Broker(registry)).held() as grant:
            assert grant.capacity[BOX_SEATS.name].value == 1

    def test_the_lock_demands_no_cores_so_a_wide_run_is_not_refused_by_its_own_lock(self, slots) -> None:
        """An unmeasured box's cpu ceiling is one, and a CPU-saturating run wants the whole width.

        Demanding cores would refuse exactly the runs this lock exists to serialise, so the seat is
        the exclusion and cores stay a caller's declaration.
        """
        with BoxLock('gate').held() as grant:
            assert 'cpu' not in grant.demands

    def test_a_caller_can_declare_what_else_it_expects_to_consume(self, slots) -> None:
        """The lock excludes; it does not decide what a consumer's run costs. That is passed on."""
        roomy = Broker(read_memory=lambda: SystemMemory(total_bytes=64 * GIB, available_bytes=48 * GIB))
        with BoxLock('gate', demands={MEMORY.name: 4096}, broker=roomy).held() as grant:
            assert grant.demands[MEMORY.name] == 4096


class TestAskingIsNotTaking:
    def test_holders_reports_the_live_holder(self, slots) -> None:
        assert BoxLock.holders() == ()
        with BoxLock('gate:integrate/main').held():
            named = BoxLock.holders()
        assert len(named) == 1
        assert named[0].what == 'gate:integrate/main'

    def test_reading_the_holders_does_not_make_the_reader_a_holder(self, slots) -> None:
        """A probe that tested takeability by ACQUIRING became a writer of the state it reported.

        Two agents reading it then refused each other over a phantom holder.
        """
        BoxLock.holders()
        BoxLock.holders()
        assert BoxLock.holders() == ()
        with BoxLock('gate').held():
            pass

    def test_the_records_live_outside_any_repository(self, slots) -> None:
        """Per-box runtime state naming a pid is not a tree's business.

        It is what makes every worktree on the box contend through ONE set of records, whatever
        revision it has checked out.
        """
        assert BoxLock.resource_dir() == slots
        assert REPO_ROOT not in BoxLock.resource_dir().parents


class TestAnUnreadableSeatIsHeldNotTaken:
    """The condition PLANTED at both namespaces the lock contends on, and the REAL guard called.

    An empty seat file is not a contrived fixture: it is exactly what the acquisition path left
    behind for an instant while the record was written after the exclusive create, and a peer that
    read it as "nobody is here" DELETED a live seat and took its index -- two holders on a lock that
    admits one (measured 2026-09-15). So the assertions are two: the guard refuses, and the file it
    refused over is STILL THERE. Unlinking it is the theft the fix forbids, and a test that only
    checked the refusal would pass on a fix that stole the seat a moment later.
    """

    def test_an_empty_pool_seat_is_not_read_as_free(self, slots) -> None:
        planted = slots / seat(BOX_POOL)
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text('', encoding='utf-8')
        assert len(BoxLock.holders()) == 1
        with pytest.raises(Exhausted) as refusal, BoxLock('heavy:feat/structural').held():
            pass
        assert refusal.value.dimension == SEATS.name
        assert planted.exists(), 'the unreadable seat was DELETED -- that is the theft, not a fix'
        assert not (slots / box_seat()).exists(), 'a refused admission left a seat behind'

    def test_an_empty_box_seat_is_not_read_as_free(self, slots) -> None:
        planted = slots / box_seat()
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text('not a record at all', encoding='utf-8')
        assert len(BoxLock.holders()) == 1
        with pytest.raises(Exhausted) as refusal, BoxLock('heavy:feat/structural').held():
            pass
        assert refusal.value.dimension == BOX_SEATS.name
        assert planted.exists()


class TestWhatMakesTheLockBoxWide:
    def test_a_second_POOL_cannot_take_the_box_while_the_lock_holds_it(self, slots) -> None:
        """THE BOX-WIDE CLAIM, MADE TRUE.

        "Box-wide" used to be a property of every caller passing the same pool string, so a consumer that named its
        own pool silently opted out of contending
        -- and nothing in the broker could tell it so.

        This is the same run, under a pool name of its own, and it is refused: the exclusion is
        ``BOX_SEATS``, a dimension whose record files are the BOX's rather than any pool's, so it
        does not depend on a spelling. The other side of the same ratchet -- that a POOL-scoped
        dimension still does not contend across pools -- is pinned in ``test_resources``.
        """
        broker = Broker()
        with (
            BoxLock('the kit').held(),
            pytest.raises(Exhausted) as refusal,
            broker.admit('some-other-pool', {BOX_SEATS.name: 1}, what='a run that opted out'),
        ):
            pass
        assert refusal.value.dimension == BOX_SEATS.name
        assert 'the kit' in str(refusal.value)

    def test_the_box_seat_is_free_again_once_the_holder_releases_it(self, slots) -> None:
        """A box-wide exclusion that outlives its holder would be a box nobody can use."""
        broker = Broker()
        with BoxLock('first').held():
            pass
        with broker.admit('some-other-pool', {BOX_SEATS.name: 1}, what='a peer') as grant:
            assert grant.demands[BOX_SEATS.name] == 1
            assert grant.basis[BOX_SEATS.name] is Basis.CONSERVATIVE_DEFAULT
