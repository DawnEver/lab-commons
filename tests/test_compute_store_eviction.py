"""L1: the local tier keeps a free-space FLOOR on its volume, evicting the least recently hit result first (D54)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_compute_store import Shared

from lab_commons.compute import floor
from lab_commons.compute.store import Record, Result, ResultStore, Tombstone
from lab_commons.headroom import Floor

PAYLOAD = 1000  # bytes per result's one file; the record adds a little


def unit(n: int) -> str:
    return f'{n:02x}' + 'e' * 62


def record_for(n: int, *, local_only: bool = False) -> Record:
    return Record(unit(n), 'ours.solve', 'v', 'env', 'h1', 't0', 't1', '', local_only)


def put(store: ResultStore, n: int) -> Result:
    return store.put_files({'out.bin': bytes([n]) * PAYLOAD}, record_for(n))


def live(store: ResultStore) -> set[int]:
    return {n for n in range(8) if (store.path(unit(n)) / 'record.json').is_file()}


class Volume:
    """A faked volume of ``capacity`` bytes: its free space is the capacity minus what the store holds."""

    def __init__(self, root: Path) -> None:
        """A volume holding the store at *root*, sized by :meth:`sized`."""
        self.root, self.capacity = root, 0

    def free(self, _where: Path) -> int:
        held = sum(one.stat().st_size for one in self.root.rglob('*') if one.is_file()) if self.root.exists() else 0
        return self.capacity - held

    def sized(self, capacity: int) -> Path:
        self.capacity = capacity
        return self.root


@pytest.fixture
def volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Volume:
    faked = Volume(tmp_path / 'store')
    monkeypatch.setattr(floor, 'free_bytes', faked.free)
    return faked


def test_without_a_floor_nothing_is_evicted(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    for n in range(4):
        put(store, n)
    assert live(store) == {0, 1, 2, 3}


def test_free_above_the_floor_evicts_nothing(volume: Volume) -> None:
    """PLANTED: the floor is met with room to spare, so every result stays."""
    store = ResultStore(volume.sized(100 * PAYLOAD), free_floor=Floor(10 * PAYLOAD))
    for n in range(4):
        put(store, n)
    assert live(store) == {0, 1, 2, 3}


def test_below_the_floor_the_least_recently_hit_result_goes_first(volume: Volume) -> None:
    store = ResultStore(volume.sized(int(PAYLOAD * 5.5)), free_floor=Floor(int(PAYLOAD * 2.5)))
    for n in range(3):
        put(store, n)
    assert live(store) == {1, 2}


def test_a_hit_refreshes_recency(volume: Volume) -> None:
    """PLANTED: without the refresh, 0 (the oldest put) would be the one evicted."""
    store = ResultStore(volume.sized(int(PAYLOAD * 5.5)), free_floor=Floor(int(PAYLOAD * 2.5)))
    put(store, 0)
    put(store, 1)
    assert isinstance(store.get(unit(0)), Result)
    put(store, 2)
    assert live(store) == {0, 2}


def test_a_write_that_cannot_keep_the_floor_is_refused_by_name(volume: Volume) -> None:
    store = ResultStore(volume.sized(100 * PAYLOAD), free_floor=Floor(3 * PAYLOAD))
    put(store, 0)
    volume.sized(int(PAYLOAD * 3.5))  # the disk fills from elsewhere: one result alone now breaks the floor
    with pytest.raises(floor.BelowFloor, match=r'floor 3000 bytes.*free -?\d+ bytes'):
        put(store, 1)
    assert live(store) == set()  # the refused write is not kept; the older result went making room
    assert not any((volume.root / '.staging').iterdir())


def test_an_evicted_result_is_a_miss_never_a_tombstone_and_graves_are_kept(volume: Volume) -> None:
    store = ResultStore(volume.sized(10 * PAYLOAD), free_floor=Floor(int(PAYLOAD * 6.5)))
    put(store, 0)
    grave = store.invalidate(unit(0), 'wrong deck', host='h1')
    put(store, 0)
    put(store, 1)
    assert store.get(unit(0)) == grave  # evicted live result: the latest tombstone answers, as before it was put
    assert len(store.history(unit(0))) == 1
    assert store.get(unit(1)) is not None
    assert not any((volume.root / '.staging').iterdir())  # eviction leaves nothing behind


def test_an_evicted_shareable_result_comes_back_from_the_shared_tier(tmp_path: Path, volume: Volume) -> None:
    shared = Shared(tmp_path / 'shared')
    store = ResultStore(volume.sized(int(PAYLOAD * 5.5)), shared, free_floor=Floor(int(PAYLOAD * 3.5)))
    put(store, 0)
    put(store, 1)
    assert live(store) == {1}
    back = store.get(unit(0))
    assert isinstance(back, Result)
    assert not isinstance(back, Tombstone)
    assert back.files['out.bin'].read_bytes() == bytes([0]) * PAYLOAD
    assert live(store) == {0}  # the arrival is the newest hit; 1 made room


def test_the_retired_cap_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r'cap_bytes is retired.*free_floor'):
        ResultStore(tmp_path / 'store', cap_bytes=10)


def test_the_door_evicts_oldest_first_through_the_callers_evict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one door a cache that is not a ResultStore (a deck cache) uses: its entries and its own evict."""
    entries = {tmp_path / name: size for name, size in (('a', 40), ('b', 30), ('c', 20))}
    monkeypatch.setattr(floor, 'free_bytes', lambda _v: 100 - sum(entries.values()))
    gone: list[str] = []

    def evict(path: Path) -> None:
        gone.append(path.name)
        del entries[path]

    held = [(1, tmp_path / 'a', 40), (3, tmp_path / 'c', 20), (2, tmp_path / 'b', 30)]
    floor.keep(tmp_path, held, Floor(50), evict=evict, keep=tmp_path / 'c')
    assert gone == ['a']  # 10 free -> a (oldest) goes -> 50 free: the floor holds and b stays


def test_the_door_never_evicts_the_kept_entry_and_refuses_when_out_of_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED: the newest (kept) entry is never a candidate, so an unreachable floor refuses instead."""
    monkeypatch.setattr(floor, 'free_bytes', lambda _v: 5)
    gone: list[Path] = []
    with pytest.raises(floor.BelowFloor, match='floor 50 bytes'):
        floor.keep(tmp_path, [(1, tmp_path / 'only', 10)], Floor(50), evict=gone.append, keep=tmp_path / 'only')
    assert gone == []
