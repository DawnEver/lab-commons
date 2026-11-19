"""L1: the local tier holds at most a cap the caller passes in, evicting the least recently hit result first."""

from __future__ import annotations

from pathlib import Path

from test_compute_store import Shared

from lab_commons.compute.store import Record, Result, ResultStore, Tombstone

PAYLOAD = 1000  # bytes per result's one file; the record adds a little


def unit(n: int) -> str:
    return f'{n:02x}' + 'e' * 62


def record_for(n: int, *, local_only: bool = False) -> Record:
    return Record(unit(n), 'ours.solve', 'v', 'env', 'h1', 't0', 't1', '', local_only)


def put(store: ResultStore, n: int) -> Result:
    return store.put_files({'out.bin': bytes([n]) * PAYLOAD}, record_for(n))


def live(store: ResultStore) -> set[int]:
    return {n for n in range(8) if (store.path(unit(n)) / 'record.json').is_file()}


def test_without_a_cap_nothing_is_evicted(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    for n in range(4):
        put(store, n)
    assert live(store) == {0, 1, 2, 3}


def test_over_the_cap_the_least_recently_hit_result_goes_first(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store', cap_bytes=int(PAYLOAD * 2.9))
    for n in range(3):
        put(store, n)
    assert live(store) == {1, 2}


def test_a_hit_refreshes_recency(tmp_path: Path) -> None:
    """PLANTED: without the refresh, 0 (the oldest put) would be the one evicted."""
    store = ResultStore(tmp_path / 'store', cap_bytes=int(PAYLOAD * 2.9))
    put(store, 0)
    put(store, 1)
    assert isinstance(store.get(unit(0)), Result)
    put(store, 2)
    assert live(store) == {0, 2}


def test_the_result_just_put_stays_even_alone_over_the_cap(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store', cap_bytes=PAYLOAD // 2)
    put(store, 0)
    put(store, 1)
    assert live(store) == {1}


def test_an_evicted_result_is_a_miss_never_a_tombstone_and_graves_are_kept(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store', cap_bytes=int(PAYLOAD * 1.5))
    put(store, 0)
    grave = store.invalidate(unit(0), 'wrong deck', host='h1')
    put(store, 0)
    put(store, 1)
    assert store.get(unit(0)) == grave  # evicted live result: the latest tombstone answers, as before it was put
    assert len(store.history(unit(0))) == 1
    assert store.get(unit(1)) is not None
    assert not any((tmp_path / 'store' / '.staging').iterdir())  # eviction leaves nothing behind


def test_an_evicted_shareable_result_comes_back_from_the_shared_tier(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'shared')
    store = ResultStore(tmp_path / 'store', shared, cap_bytes=int(PAYLOAD * 1.5))
    put(store, 0)
    put(store, 1)
    assert live(store) == {1}
    back = store.get(unit(0))
    assert isinstance(back, Result)
    assert not isinstance(back, Tombstone)
    assert back.files['out.bin'].read_bytes() == bytes([0]) * PAYLOAD
    assert live(store) == {0}  # the arrival is the newest hit; 1 made room
