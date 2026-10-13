"""RESERVATIONS IN THE FORGE LEDGER: written only by the host's own login or a delegate, read by every host.

User ruling 2026-10-13: only the host itself (its own user) or a login that user designates may reserve
it. The forge records who wrote each comment, so the check is made by EVERY READER against the comment's
forge-recorded author -- a body claiming another writer, or a writer the card does not name, counts for
nothing. The writer is also refused up front, so a refused reservation is never posted.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from lab_commons.compute import ledger
from lab_commons.compute.admission import admit
from lab_commons.compute.model import For, Job, Resources, Supply
from lab_commons.dev import forgequeue
from lab_commons.dev.forgehosts import HostCard, publish
from tests._forge_state import StateForge

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)  # the StateForge's clock
LATER = NOW + timedelta(hours=24)


def _forge_with(host: str = 'box-g', owner: str = 'alice', delegates: tuple[str, ...] = ('bob',)) -> StateForge:
    forge = StateForge()
    forge.login = owner
    publish(forge, HostCard(host, owner, 'windows', NOW.isoformat(), delegates=delegates))
    return forge


def _ask(scope: str = 'box-g', **amounts: float) -> ledger.Ask:
    return ledger.Ask(scope, Resources(amounts or {'cpu': 8}), For(repo='o/r'), LATER)


def test_the_hosts_own_login_reserves_and_every_reader_sees_it() -> None:
    forge = _forge_with()
    made = ledger.reserve(forge, _ask(cpu=8, mem_gb=32))
    seen = ledger.read(forge)
    assert [one.reservation for one in seen.live(NOW)] == [made.reservation]
    assert made.reservation.by == 'alice'
    assert made.reservation.resources == Resources(cpu=8, mem_gb=32)


def test_a_designated_login_reserves() -> None:
    forge = _forge_with()
    forge.login = 'bob'
    assert ledger.reserve(forge, _ask()).reservation.by == 'bob'


def test_any_other_login_is_refused_and_nothing_is_posted() -> None:
    """PLANTED CONTROL: the same ask from the owner is accepted (the first test)."""
    forge = _forge_with()
    forge.login = 'mallory'
    with pytest.raises(ledger.ReserveRefused, match=r'mallory may not reserve box-g.*alice.*bob'):
        ledger.reserve(forge, _ask())
    assert ledger.read(forge).entries == ()


def test_a_host_with_no_card_cannot_be_reserved() -> None:
    forge = _forge_with()
    with pytest.raises(ledger.ReserveRefused, match='no host card'):
        ledger.reserve(forge, _ask('box-x'))


def test_a_forged_reservation_counts_for_nothing_at_every_reader() -> None:
    """A writer that skips the CLI check: the reader judges the forge-recorded author, not the body."""
    forge = _forge_with()
    forge.login = 'mallory'
    number = ledger.read(forge).number
    assert number is not None
    forged = ledger.Entry(0, ledger.Ask('box-g', Resources(cpu=64), For(login='mallory'), LATER).signed('alice'))
    forge.call('POST', f'{forge.repo}/issues/{number}/comments', {'body': forged.text()})
    seen = ledger.read(forge)
    assert seen.live(NOW) == ()
    (refused,) = seen.entries
    assert 'mallory may not reserve box-g' in refused.refused
    assert refused.reservation.by == 'mallory', 'the author is the forge record, never the body'


def test_an_expired_reservation_is_not_live() -> None:
    forge = _forge_with()
    ledger.reserve(forge, _ask())
    assert ledger.read(forge).live(LATER) == ()


def test_release_removes_it_and_only_an_authorised_login_may() -> None:
    forge = _forge_with()
    made = ledger.reserve(forge, _ask())
    forge.login = 'mallory'
    with pytest.raises(ledger.ReserveRefused, match='mallory may not release'):
        ledger.release(forge, made.id)
    forge.login = 'bob'
    ledger.release(forge, made.id)
    assert ledger.read(forge).entries == ()
    with pytest.raises(ledger.ReserveRefused, match='no reservation'):
        ledger.release(forge, made.id)


def test_a_live_reservation_lends_its_idle_part_under_its_ledger_id() -> None:
    forge = _forge_with()
    made = ledger.reserve(forge, _ask(cpu=12))
    held = [one.reservation for one in ledger.read(forge).live(NOW)]
    assert [one.id for one in held] == [made.id]
    supply = Supply('box-g', Resources(cpu=16))
    other = Job('o/x', 'a' * 40, 'pytest')
    mine = Job('o/r', 'a' * 40, 'pytest')
    assert admit(other, Resources(cpu=8), supply, reservations=held, now=NOW).on_loan == made.id
    assert admit(mine, Resources(cpu=8), supply, reservations=held, now=NOW).on_loan == 0


def test_a_whole_scope_lease_round_trips_and_holds_the_supply_as_read_now() -> None:
    forge = _forge_with()
    ledger.reserve(forge, ledger.Ask('box-g', Resources(), For(repo='o/r'), LATER, whole=True))
    (entry,) = ledger.read(forge).live(NOW)
    assert entry.reservation.whole
    assert entry.row()['resources'] == 'all'
    assert entry.reservation.amount(Supply('box-g', Resources(cpu=32))) == Resources(cpu=32)
    with pytest.raises(ValueError, match='either the whole scope or some resources'):
        dataclasses.replace(entry.reservation, resources=Resources(cpu=1))


# -- the CLI spelling -----------------------------------------------------------------------------------


def test_the_cli_spelling_parses_amounts_whom_and_until() -> None:
    assert ledger.parse_resources('cpu=8,mem_gb=32.5,jmag=1') == Resources(cpu=8, mem_gb=32.5, jmag=1)
    assert ledger.parse_for('login:bob') == For(login='bob')
    assert ledger.parse_for('repo:o/r') == For(repo='o/r')
    assert ledger.parse_for('host:G') == For(host='G')
    assert ledger.parse_for('session:lane/x') == For(session='lane/x')
    assert ledger.parse_for('needs:jmag+windows') == For(needs=frozenset({'jmag', 'windows'}))
    assert ledger.parse_until('36h', NOW) == NOW + timedelta(hours=36)
    assert ledger.parse_until('2d', NOW) == NOW + timedelta(days=2)
    assert ledger.parse_until('2026-10-13T08:00:00Z', NOW) == datetime(2026, 10, 13, 8, tzinfo=UTC)
    for bad in ('cpu', 'cpu=-1', ''):
        with pytest.raises(ValueError, match='resource'):
            ledger.parse_resources(bad)
    with pytest.raises(ValueError, match='login:'):
        ledger.parse_for('team:x')
    with pytest.raises(ValueError, match='past'):
        ledger.parse_until('2026-10-01T00:00:00Z', NOW)


# -- the own reserve --------------------------------------------------------------------------------------


def test_the_own_reserve_is_what_the_user_uses_now_plus_a_margin_capped_at_the_box() -> None:
    table = ledger.reserve_table({})
    assert table.mem_margin == ledger.DEFAULT_MEM_MARGIN == 0.25
    total = Resources(cpu=16, mem_gb=64)
    own = ledger.own_reserve(total, Resources(cpu=3, mem_gb=10), table)
    assert own == Resources(cpu=3 + ledger.DEFAULT_CPU_MARGIN, mem_gb=10 + 16)
    assert ledger.own_reserve(total, Resources(cpu=16, mem_gb=60), table) == total, 'never more than the box'


def test_the_reserve_table_declares_delegates_and_margins_and_refuses_the_rest() -> None:
    table = ledger.reserve_table({'delegates': ['bob'], 'cpu_margin': 2, 'mem_margin': 0.5})
    assert (table.delegates, table.cpu_margin, table.mem_margin) == (('bob',), 2.0, 0.5)
    for bad in ({'delegate': ['bob']}, {'mem_margin': 1.5}, {'cpu_margin': -1}, {'delegates': 'bob'}):
        with pytest.raises(ValueError, match=r'\[reserve\]'):
            ledger.reserve_table(bad)


def test_the_queue_lists_each_live_reservation_and_flags_a_refused_writer() -> None:
    forge = _forge_with()
    made = ledger.reserve(forge, _ask(cpu=8))
    held, refused = made.row(), {**made.row(), 'id': 7, 'by': 'mallory', 'refused': 'mallory may not reserve box-g'}
    lines = forgequeue.render({'now': NOW.isoformat(), 'queue': [], 'in_flight': [], 'hosts': [], 'starved': [],
                               'reservations': [held, refused]})  # fmt: skip
    at = lines.index('reservations:')
    assert lines[at + 1].startswith(f'  {made.id}  box-g  cpu=8  for repo:o/r  until ')
    assert lines[at + 1].endswith('by alice')
    assert lines[at + 2].endswith('REFUSED: mallory may not reserve box-g')


def test_a_host_and_a_session_lease_survive_the_ledger() -> None:
    forge = _forge_with()
    for whom in (For(host='box-g'), For(session='lane/x')):
        ledger.reserve(forge, ledger.Ask('box-g', Resources(cpu=1), whom, LATER))
    seen = [one.reservation.for_ for one in ledger.read(forge).live(NOW)]
    assert For(host='box-g') in seen
    assert For(session='lane/x') in seen
    assert {one.row()['for'] for one in ledger.read(forge).entries} >= {'host:box-g', 'session:lane/x'}
