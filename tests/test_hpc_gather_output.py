"""``hpc gather`` never prints a bare success line with no artifact (dev-lab #483)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lab_commons.hpc import __main__ as hpc_main
from lab_commons.hpc.jobs import Gathered

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_RECORD = {'placements': [{'shards': 2}]}


def _gathered(monkeypatch: pytest.MonkeyPatch) -> None:
    got = Gathered(results=[{'index': 0, 'ok': True, 'status': 'ok'}], returned=2, silent=[], over=[], earlier=[])
    monkeypatch.setattr(hpc_main, 'gather_job', lambda *_a, **_k: got)


def test_a_gather_without_an_output_says_nothing_was_written_and_names_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _gathered(monkeypatch)
    assert hpc_main._gather(_RECORD, None, None, None, 0) == 0
    out = capsys.readouterr().out
    assert 'nothing written' in out
    assert '-o' in out


def test_planted_control_a_gather_with_an_output_writes_it_and_says_where(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _gathered(monkeypatch)
    target = tmp_path / 'merged.json'
    assert hpc_main._gather(_RECORD, None, None, target, 0) == 0
    assert json.loads(target.read_text(encoding='utf-8'))[0]['index'] == 0
    out = capsys.readouterr().out
    assert f'written {target}' in out
    assert 'nothing written' not in out
