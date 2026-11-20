"""The box refuses a run whose volume is below the machine's free-space floor, and warns under the watermark."""

from __future__ import annotations

from pathlib import Path

import pytest

import lab_commons.compute.floor as floor_module
from lab_commons.box import disk, ledger
from lab_commons.box.demand import Refused
from lab_commons.headroom import Floor

_GIB = 1024**3
_FLOOR = Floor(bytes=50 * _GIB)


def _free(monkeypatch: pytest.MonkeyPatch, gib: float) -> None:
    monkeypatch.setattr(floor_module, 'free_bytes', lambda _volume: int(gib * _GIB))


def test_a_roomy_volume_is_neither_refused_nor_warned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PLANTED CONTROL: well above twice the floor, the verdict is clear."""
    _free(monkeypatch, 500)
    assert disk.verdict([tmp_path], _FLOOR) == (None, None)


def test_a_near_full_volume_is_refused_by_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _free(monkeypatch, 0.9)
    refusal, warning = disk.verdict([tmp_path], _FLOOR)
    assert warning is None
    assert refusal is not None
    assert '0.9 GiB free' in refusal
    assert 'floor 50.0 GiB' in refusal
    assert 'free_floor_gb' in refusal


def test_under_the_watermark_warns_but_admits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _free(monkeypatch, 70)
    refusal, warning = disk.verdict([tmp_path], _FLOOR)
    assert refusal is None
    assert warning is not None
    assert 'watermark 100.0 GiB' in warning


def test_one_volume_is_measured_once(tmp_path: Path) -> None:
    """Two paths on one device are one volume; a path not yet created is measured at its nearest parent."""
    found = disk.volumes([tmp_path, tmp_path / 'not' / 'yet'])
    assert found == [tmp_path]


def test_hold_refuses_at_once_on_a_full_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, 'free_floor', lambda: _FLOOR)
    _free(monkeypatch, 0.5)
    with (
        pytest.raises(Refused, match='below the free-space floor'),
        ledger.hold({'cpu': 1, 'mem_gb': 0.1}, 60, 'disk-test', queue_s=0, key='k'),
    ):
        pass


def test_hold_admits_when_the_volume_is_roomy(monkeypatch: pytest.MonkeyPatch) -> None:
    """PLANTED CONTROL: the same hold on a roomy volume is admitted."""
    monkeypatch.setattr(ledger, 'free_floor', lambda: _FLOOR)
    _free(monkeypatch, 500)
    with ledger.hold({'cpu': 1, 'mem_gb': 0.1}, 60, 'disk-test', queue_s=0, key='k') as claim:
        assert claim.id
