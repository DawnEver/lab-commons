"""``lab_commons.dev.boxlock`` — one CPU-saturating run at a time, refusing by name.

The lock is the broker that already ships in this package, so what is tested here is the POLICY:
that the pool name is the exclusion, that a per-box seat measurement cannot lift it, and that a
refusal arrives as the broker's own ``Exhausted`` -- carrying the holder's name -- rather than as a
second exception class that a caller would have to learn.

The slot root is redirected per test, because "box-wide" is the property under test and cannot be
mocked away without testing nothing.
"""

from pathlib import Path

import pytest

from lab_commons.dev.boxlock import BOX_POOL, BoxLock
from lab_commons.resources import (
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
def slots(tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_COMMONS_RESOURCE_DIR', str(tmp_path / 'slots'))
    return tmp_path / 'slots'


def _take_a_second_seat(what: str, broker: Broker | None = None) -> None:
    """Attempt to hold the box while somebody else already does. A helper, not a nested ``with``.

    Written as a call because the assertion lives in the OUTER block: flattening the two contexts
    into one ``with`` would put the ``pytest.raises`` around the very statements that must not
    raise.
    """
    with BoxLock(what, broker=broker).held():
        pytest.fail(f'{what} was admitted while the box was already held')


class TestTheExclusion:
    def test_a_second_holder_is_refused_and_the_refusal_names_the_first(self, slots):
        """THE DECISIVE ONE. "Busy" sends its reader to the process table to guess, and guessing
        wrong kills somebody's evidence."""
        with BoxLock('gate:integrate/main').held(), pytest.raises(Exhausted) as refusal:
            _take_a_second_seat('heavy:feat/structural')
        assert 'gate:integrate/main' in str(refusal.value)
        assert refusal.value.pool == BOX_POOL

    def test_the_seat_is_free_again_once_the_holder_releases_it(self, slots):
        with BoxLock('first').held():
            pass
        with BoxLock('second').held() as grant:
            assert grant.pool == BOX_POOL

    def test_a_dead_holders_record_is_free_not_held(self, slots):
        """The worst outcome of any crash is a file nobody counts, never a box nobody can use."""
        dead = slots / f'{BOX_POOL}.0.slot'
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text('{"job_kind": "pid", "job_ident": "999999999", "demands": {}}', encoding='utf-8')
        with BoxLock('after a crash').held():
            pass

    def test_a_holder_with_no_name_is_refused_at_construction(self):
        with pytest.raises(ValueError, match='cannot be acted on'):
            BoxLock('   ')


class TestWhatTheLockDeclares:
    def test_the_seat_rests_on_a_structural_constant_not_on_a_box_measurement(self, slots):
        """The rule is a property of the TOOL -- one at a time, everywhere -- not of this machine."""
        with BoxLock('gate').held() as grant:
            assert grant.basis[SEATS.name] is Basis.STRUCTURAL
            assert grant.capacity[SEATS.name].value == 1

    def test_a_per_box_measurement_of_four_seats_cannot_lift_it(self, slots):
        """Declarations compose by taking the SMALLEST, so the structural constant always wins."""
        registry = CapacityRegistry()
        registry.declare(BOX_POOL, Capacity.measured(SEATS.name, 4, on=Broker().hostname))
        broker = Broker(registry)
        with BoxLock('gate', broker=broker).held() as grant:
            conceded = grant.capacity[SEATS.name].value
            with pytest.raises(Exhausted):
                _take_a_second_seat('heavy', broker)
        assert conceded == 1

    def test_the_lock_demands_no_cores_so_a_wide_run_is_not_refused_by_its_own_lock(self, slots):
        """An unmeasured box's cpu ceiling is one, and a CPU-saturating run wants the whole width.

        Demanding cores would refuse exactly the runs this lock exists to serialise, so the seat is
        the exclusion and cores stay a caller's declaration.
        """
        with BoxLock('gate').held() as grant:
            assert 'cpu' not in grant.demands

    def test_a_caller_can_declare_what_else_it_expects_to_consume(self, slots):
        """The lock excludes; it does not decide what a consumer's run costs. That is passed on."""
        roomy = Broker(read_memory=lambda: SystemMemory(total_bytes=64 * GIB, available_bytes=48 * GIB))
        with BoxLock('gate', demands={MEMORY.name: 4096}, broker=roomy).held() as grant:
            assert grant.demands[MEMORY.name] == 4096


class TestAskingIsNotTaking:
    def test_holders_reports_the_live_holder(self, slots):
        assert BoxLock.holders() == ()
        with BoxLock('gate:integrate/main').held():
            named = BoxLock.holders()
        assert len(named) == 1
        assert named[0].what == 'gate:integrate/main'

    def test_reading_the_holders_does_not_make_the_reader_a_holder(self, slots):
        """A probe that tested takeability by ACQUIRING became a writer of the state it reported,
        and two agents reading it refused each other over a phantom holder."""
        BoxLock.holders()
        BoxLock.holders()
        assert BoxLock.holders() == ()
        with BoxLock('gate').held():
            pass

    def test_the_records_live_outside_any_repository(self, slots):
        """Per-box runtime state naming a pid is not a tree's business, and it is what makes every
        worktree on the box contend through ONE set of records whatever revision it has checked out."""
        assert BoxLock.resource_dir() == slots
        assert REPO_ROOT not in BoxLock.resource_dir().parents


class TestWhatTheLockInheritsFromTheBroker:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            'Broker.admit creates the seat file with O_EXCL and writes its RECORD afterwards, so '
            'between os.close and Grant._write_record the file is EMPTY. _holder_of reads an '
            "unparseable record as ABSENT (json.loads('') raises, and the OSError/ValueError arm "
            'returns None), and _take_seats UNLINKS what it read as absent before trying O_EXCL -- '
            'so a peer can delete a seat another process has just taken and take the same index. '
            'The conservative rule the module already applies to an opaque job handle ("cannot '
            'tell" is HELD, never free) is exactly what an unreadable record needs. Fix in '
            'lab_commons/resources.py, not here: this test is the pin, and it is strict so a fix '
            'that lands forces this row to be deleted rather than left to rot.'
        ),
    )
    def test_an_acquisition_in_progress_is_not_read_as_a_free_seat(self, slots):
        """An empty seat file is exactly what ``os.open(O_CREAT | O_EXCL)`` leaves behind."""
        slots.mkdir(parents=True, exist_ok=True)
        (slots / f'{BOX_POOL}.0.slot').write_text('', encoding='utf-8')
        assert len(BoxLock.holders()) == 1


class TestWhatThePoolNameIs:
    def test_a_different_pool_name_does_not_contend(self, slots):
        """THE FINDING, demonstrated: "box-wide" is not something the broker can enforce.

        The broker rations PER POOL, and a pool is a string. A consumer that passes its own name has
        silently opted out of contending with the others, and nothing in the broker can tell it so --
        which is why ``BOX_POOL`` is a constant this module owns and publishes.
        """
        broker = Broker()
        with (
            BoxLock('the kit').held(),
            broker.admit('some-other-pool', {SEATS.name: 1}, what='a run that opted out'),
        ):
            assert len(BoxLock.holders()) == 1
