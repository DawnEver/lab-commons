"""A cluster item's outcome contract: a FAILED item's completed work rides back beside its reason."""

from __future__ import annotations

import json
from pathlib import Path

from lab_commons.hpc.cluster import gather
from lab_commons.hpc.worker import RESULTS, Failed, run_shard
from lab_commons.outcomes import Reason, Status
from lab_commons.outcomes.jsonl import read_jsonl
from lab_commons.outcomes.record import Event

_CLOSED = json.dumps(Event('shard', '0', data={'code': 0, 'tail': []}).to_plain()) + '\n'


def _entry(item: int) -> int:
    if item < 0:
        raise Failed(Reason('budget', f'{item} spent past its cap'), {'rows': [1, 2]})
    if item == 0:
        raise ZeroDivisionError(item)
    return item


def _gathered(results: Path) -> list[dict]:
    text = results.read_text(encoding='utf-8') + _CLOSED

    def login(_command: str, _stdin: str | None = None) -> str:
        return text + '\n@@@ earlier-layout\n'

    return gather(login, '~/r')[0]


def test_a_failed_item_returns_its_completed_rows_beside_its_reason(tmp_path: Path) -> None:
    """A :class:`Failed` item is an ERROR whose value rides back; a bare crash carries none (control)."""
    results = tmp_path / RESULTS
    run_shard({'items': [3, -1, 0], 'shards': [[0, 3]], 'lanes': []}, 0, results, entry=_entry)
    events = [Event.from_plain(row) for row in read_jsonl(results).rows]
    assert [e.cell.status for e in events] == [Status.OK, Status.ERROR, Status.ERROR]
    assert events[1].cell.reason == Reason('budget', '-1 spent past its cap')
    assert events[1].data['value'] == {'rows': [1, 2]}
    assert 'value' not in events[2].data
    got = _gathered(results)
    assert got[0] == {'index': 0, 'ok': True, 'status': 'ok', 'value': 3}
    assert got[1] == {
        'index': 1,
        'ok': False,
        'status': 'error',
        'error': '-1 spent past its cap',
        'value': {'rows': [1, 2]},
    }
    assert 'value' not in got[2]
    assert 'ZeroDivisionError' in got[2]['error']
