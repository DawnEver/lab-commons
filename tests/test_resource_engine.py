"""ONE RESOURCE PATH, P0: the engine admits, holds, charges, releases and reaps every KIND by one script.

The three kinds differ only in their supply formula (the provider's) and their return moment (the engine's):
OCCUPANCY returns at run end, CONSUMABLE charges its actual and returns the remainder of its cap, PERSISTENT
returns only when reaped. One script runs against a fake provider of each kind and reads the free amount at
each step; a consumable past its balance is refused (the planted control).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager

import pytest

from lab_commons.box import GRACE_S, holdings
from lab_commons.box._store import box_path, connect
from lab_commons.compute.model import Resources, Supply
from lab_commons.config import config_path
from lab_commons.resource import KINDS, Actual, Claim, Engine, Kind, NotAdmitted, charged, kind_of

_SCOPE = 'fake'


class _Fake:
    """A provider of one name: *size* per measure (a consumable's less what the engine charged in its window)."""

    def __init__(self, name: str, size: float, actual: float) -> None:
        self.names = frozenset({name})
        self.name, self.size, self.actual = name, size, actual

    def measure(self, scope: str) -> Supply:
        spent = charged(scope, self.name) if KINDS[self.name] is Kind.CONSUMABLE else 0.0
        return Supply(scope, Resources({self.name: self.size - spent}), self.names)

    @contextmanager
    def enforce(self, scope: str, claim: Claim) -> Iterator[Actual]:
        actual = Actual()
        yield actual
        actual.used = Resources({self.name: self.actual})


@pytest.fixture(autouse=True)
def _windows() -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[resources.tokens]\nwindow = "1d"\n', encoding='utf-8')


def _free(engine: Engine, name: str) -> float:
    return engine.free(_SCOPE)[name]


#: name, demand, actual, (free while held, after release, after reap) out of 100.
_SCRIPT = [
    pytest.param('cpu', 40, 30, (60, 100, 100), id='occupancy-returns-at-end'),
    pytest.param('tokens', 40, 30, (60, 70, 70), id='consumable-charges-actual-returns-remainder'),
    pytest.param('disk_gb', 40, 30, (60, 60, 100), id='persistent-returns-only-on-reap'),
]


@pytest.mark.parametrize(('name', 'demand', 'actual', 'expected'), _SCRIPT)
def test_one_script_admits_holds_releases_and_reaps_every_kind(
    name: str, demand: float, actual: float, expected: tuple[float, float, float]
) -> None:
    engine = Engine({_SCOPE: [_Fake(name, 100, actual)]})
    claim = Claim(Resources({name: demand}), wall_s=60, what=f'script {name}', key='script')
    assert engine.claimable(_SCOPE, claim)
    with engine.claim(_SCOPE, claim):
        held = _free(engine, name)
    released = _free(engine, name)
    engine.reap(now=time.time() + 60 + GRACE_S + 1)
    assert (held, released, _free(engine, name)) == expected


def test_a_consumable_past_its_balance_is_refused() -> None:
    """PLANTED CONTROL: 70 spent of 100 leaves 30; 31 must red, 30 must not, and a held cap is a promise."""
    engine = Engine({_SCOPE: [_Fake('tokens', 100, 70)]})
    with engine.claim(_SCOPE, Claim(Resources(tokens=80), wall_s=60, what='spender', key='a')):
        pass
    assert charged(_SCOPE, 'tokens') == 70
    with pytest.raises(NotAdmitted, match=r'tokens \(31 asked, 30 free\)'):
        engine.claim(_SCOPE, Claim(Resources(tokens=31), wall_s=60, what='over', key='b')).__enter__()
    with engine.claim(_SCOPE, Claim(Resources(tokens=20), wall_s=60, what='first', key='c')):
        assert not engine.claimable(_SCOPE, Claim(Resources(tokens=20), wall_s=60, what='second', key='d'))


def test_a_name_without_a_kind_or_a_second_provider_of_one_name_is_refused() -> None:
    with pytest.raises(ValueError, match='no kind'):
        kind_of('unicorns')
    with pytest.raises(ValueError, match='cpu'):
        Engine({_SCOPE: [_Fake('cpu', 1, 1), _Fake('cpu', 1, 1)]})


def test_a_consumable_without_a_declared_window_is_refused() -> None:
    config_path().write_text('', encoding='utf-8')
    held = Engine({_SCOPE: [_Fake('tokens', 1, 1)]}).claim(_SCOPE, Claim(Resources(tokens=1), 60, 'x', 'x'))
    with pytest.raises(ValueError, match='window'):
        held.__enter__()


def test_engine_rows_of_another_scope_are_not_the_boxs() -> None:
    with Engine({_SCOPE: [_Fake('cpu', 100, 1)]}).claim(_SCOPE, Claim(Resources(cpu=4), wall_s=60, what='x', key='x')):
        assert holdings() == []


def test_a_claims_row_written_before_kinds_existed_reads_as_box_occupancy() -> None:
    """FORMATS-UPGRADE-ON-READ: a pre-existing ``claims`` table gains scope/kind/window; its rows are box OCCUPANCY."""
    path = box_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as old:
        old.executescript(
            'CREATE TABLE claims (id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, created INTEGER,'
            ' cpu INTEGER NOT NULL, mem_bytes INTEGER NOT NULL, seats TEXT NOT NULL, deadline REAL NOT NULL,'
            ' started REAL NOT NULL, what TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT);'
            'INSERT INTO claims (pid, cpu, mem_bytes, seats, deadline, started, what, key)'
            " VALUES (1, 2, 0, '{}', 1, 0, 'old', 'k');"
        )
    with closing(connect()) as con:
        assert con.execute('SELECT scope, kind, window FROM claims').fetchall() == [('box', 'occupancy', None)]
