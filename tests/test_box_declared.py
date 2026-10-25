"""A PERSON DECLARES THEIR USE as an ordinary claim on the one ledger (ruling 2026-10-25), and the forge yields.

No core, byte or seat is reserved for people by configuration. A person (or a session) declares what their
work will use and for how long -- ``python -m lab_commons.box claim --cpu N --mem-gb G --seat jmag=K --wall 4h``
-- with no process run under it. The row lives until its wall deadline or ``box release <id>``; every
admission (a forge hold included) subtracts it like any claim. Use nobody declared is still measured.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from _box_fake import quiet

from lab_commons.box import BOX_ENV, GRACE_S, Capacity, NotAdmitted, declare, hold, holdings
from lab_commons.box.__main__ import main
from lab_commons.box._store import connect
from lab_commons.box.ledger import _reap

_CAP = Capacity(cpu=8.0, mem_bytes=16 * 2**30, seats={'jmag': 3})
_ON = {'key': 'forge', 'capacity': _CAP, 'read': quiet(_CAP)}


@pytest.fixture(autouse=True)
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'box.sqlite'
    monkeypatch.setenv(BOX_ENV, str(path))
    return path


def test_a_forge_hold_fits_on_a_box_nobody_declared() -> None:
    """PLANTED CONTROL: without the person's claim, the forge's demand is admitted."""
    with hold({'cpu': 4, 'mem_gb': 8, 'jmag': 2}, 60, 'forge jmag', 0.0, **_ON):
        pass


def test_a_declared_claim_refuses_a_forge_hold_on_seats_and_cores() -> None:
    declare({'cpu': 6, 'mem_gb': 4, 'jmag': 2}, 4 * 3600, 'designer session', owner='alice')
    with (
        pytest.raises(NotAdmitted, match='alice: designer session'),
        hold({'cpu': 1, 'mem_gb': 1, 'jmag': 2}, 60, 'forge jmag', 0.0, **_ON),
    ):
        pass
    with pytest.raises(NotAdmitted, match='cpu'), hold({'cpu': 4, 'mem_gb': 1}, 60, 'forge cpu', 0.0, **_ON):
        pass
    with hold({'cpu': 2, 'mem_gb': 1, 'jmag': 1}, 60, 'forge reduced', 0.0, **_ON):
        pass


def test_a_declared_claim_outlives_its_declaring_process_until_released() -> None:
    claim = declare({'cpu': 0, 'mem_gb': 0, 'jmag': 1}, 3600, 'a solve from the GUI', owner='alice')
    with connect() as con:
        _reap(con, time.time())
    assert [h.id for h in holdings()] == [claim.id]
    assert main(['release', str(claim.id)]) == 0
    assert holdings() == []


def test_a_declared_claim_is_reaped_at_its_deadline() -> None:
    claim = declare({'cpu': 0, 'mem_gb': 0, 'jmag': 1}, 60, 'short', owner='alice')
    with connect() as con:
        _reap(con, claim.deadline + GRACE_S + 1)
    assert holdings() == []


def test_the_cli_declares_lists_and_releases(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['claim', '--cpu', '4', '--mem-gb', '8', '--seat', 'jmag=2', '--wall', '4h', '--why', 'tuning']) == 0
    (row,) = holdings()
    assert (row.cpu, row.mem_bytes, dict(row.seats)) == (4, 8 * 2**30, {'jmag': 2})
    assert 4 * 3600 - 60 < row.deadline - time.time() <= 4 * 3600
    assert row.what.endswith(': tuning')
    assert f'claim {row.id}' in capsys.readouterr().out
    assert main(['claims', '--json']) == 0
    (listed,) = json.loads(capsys.readouterr().out)
    assert listed['id'] == row.id
    assert main(['release', str(row.id)]) == 0
    assert holdings() == []


@pytest.mark.parametrize('wall', ['0', '4x', 'h', '-1h', '8d'])
def test_a_bad_wall_is_refused(wall: str) -> None:
    with pytest.raises(SystemExit):
        main(['claim', '--cpu', '1', '--mem-gb', '1', '--wall', wall, '--why', 'x'])


def test_releasing_an_unknown_claim_says_so() -> None:
    assert main(['release', '999']) == 1
