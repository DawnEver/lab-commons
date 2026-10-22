"""``lab_commons.compute.durable`` -- a durable write leaves the whole file and no staged temp behind."""

from __future__ import annotations

from pathlib import Path

from lab_commons.compute.durable import durable_write


def test_a_durable_write_lands_whole_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / 'deep' / 'record.json'
    assert durable_write(target, b'first') == target
    durable_write(target, b'second')
    assert target.read_bytes() == b'second'
    assert sorted(p.name for p in target.parent.iterdir()) == ['record.json']
