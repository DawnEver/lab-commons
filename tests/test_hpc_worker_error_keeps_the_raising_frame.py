"""An item's ERROR reason names the frame that RAISED, not only the outermost frames of the call.

MEASURED 2026-10-24 (motronics T3 at fe44fd3841, Ada): `format_exc(limit=5)` kept the five OUTERMOST frames,
so every edge-key KeyError reached the record cut at the study driver, several frames above the mesher line
that raised it -- the cause had to be reproduced from scratch.
"""

from __future__ import annotations

from lab_commons.hpc.worker import run_shard


def _deep(depth: int) -> None:
    if depth == 0:
        msg = 'the line that raised'
        raise KeyError(msg)
    _deep(depth - 1)


def _entry(_item) -> None:
    _deep(12)


def test_an_item_error_keeps_the_innermost_frame(tmp_path) -> None:
    results = tmp_path / 'results.jsonl'
    run_shard({'shards': [(0, 1)], 'items': [{}], 'entry': 'unused:unused', 'lanes': []}, 0, results, entry=_entry)
    text = results.read_text(encoding='utf-8')
    assert '"ERROR"' in text or 'error' in text.lower()
    assert 'raise KeyError(msg)' in text, 'the raising frame is in the recorded reason'
