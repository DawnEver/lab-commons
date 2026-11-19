"""D16/D18/D19: results are content-addressed, installed atomically, first-wins, and a dirty one stays home."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from lab_commons.compute import store as store_module
from lab_commons.compute.store import (
    RECORD,
    ArrivalMismatch,
    Nondeterministic,
    Record,
    Result,
    ResultStore,
    Tombstone,
    TooLarge,
    result_hash,
)

UNIT = 'ab' + 'c' * 62


class Shared:
    """A shared tier in a directory: first post per generation wins, a differing one raises; graves are files.

    *corrupt* flips a byte of every fetched output, so an arrival check has something to refuse.
    """

    def __init__(self, root: Path, *, max_bytes: int | None = None) -> None:
        """A tier kept under *root*, counting posts."""
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.posts, self.max_bytes, self.corrupt = root, 0, max_bytes, False

    def fetch(self, unit_hash: str, generation: int, dest: Path) -> Path | None:
        held = self.root / f'{unit_hash}-g{generation}'
        if not held.is_dir():
            return None
        shutil.copytree(held, dest, dirs_exist_ok=True)
        if self.corrupt:
            for one in dest.iterdir():
                if one.name != RECORD:
                    one.write_bytes(one.read_bytes() + b'!')
        return dest

    def post(self, result: Result, generation: int) -> None:
        self.posts += 1
        held = self.root / f'{result.record.unit_hash}-g{generation}'
        if held.is_dir():
            if Record.read(held / RECORD).result_hash != result.record.result_hash:
                raise Nondeterministic(result.record.unit_hash)
            return
        shutil.copytree(result.path, held)

    def bury(self, tombstone: Tombstone) -> None:
        (self.root / f'grave-{tombstone.unit_hash}-{tombstone.generation}.json').write_bytes(tombstone.to_bytes())

    def graves(self, unit_hash: str) -> tuple[Tombstone, ...]:
        found = sorted(self.root.glob(f'grave-{unit_hash}-*.json'), key=lambda p: int(p.stem.rsplit('-', 1)[1]))
        return tuple(Tombstone.read(p) for p in found)


def staged(store: ResultStore, text: str, *, local_only: bool = False, host: str = 'h1') -> tuple[Path, Record]:
    where = store.staging()
    (where / 'out.txt').write_text(text, encoding='utf-8')
    record = Record(UNIT, 'ours.solve', 'v', 'env', host, 't0', 't1', result_hash(where, ('out.txt',)), local_only)
    return where, record


def test_put_then_get_is_the_same_result(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    put = store.put(*staged(store, '42'))
    got = store.get(UNIT)
    assert got == put
    assert got.output('out.txt').read_text(encoding='utf-8') == '42'
    assert store.get('0' * 64) is None
    assert not any((tmp_path / 'store' / '.staging').iterdir())  # the install was a rename, nothing left behind


def test_an_identical_duplicate_is_dropped(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    first = store.put(*staged(store, '42'))
    assert store.put(*staged(store, '42', host='h2')) == first  # the first stands, its record included


def test_a_differing_duplicate_alarms_and_the_first_stands(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    store.put(*staged(store, '42'))
    with pytest.raises(Nondeterministic, match='the first stands'):
        store.put(*staged(store, '43', host='h2'))
    assert store.get(UNIT).output('out.txt').read_text(encoding='utf-8') == '42'


def test_a_shareable_result_is_posted_and_another_host_reads_it(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'forge')
    here = ResultStore(tmp_path / 'here', shared)
    here.put(*staged(here, '42'))
    assert shared.posts == 1
    there = ResultStore(tmp_path / 'there', shared)
    got = there.get(UNIT)
    assert got is not None
    assert got.output('out.txt').read_text(encoding='utf-8') == '42'
    assert got.path.is_relative_to(tmp_path / 'there')  # installed locally: the next read is local


def test_a_differing_shared_result_alarms(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'forge')
    ResultStore(tmp_path / 'a', shared).put(*staged(ResultStore(tmp_path / 'a'), '42'))
    b = ResultStore(tmp_path / 'b', shared)
    with pytest.raises(Nondeterministic):
        b.put(*staged(b, '43'))


def test_a_local_only_result_never_leaves_the_box(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'forge')
    store = ResultStore(tmp_path / 'store', shared)
    where, record = staged(store, '42', local_only=True)
    store.put(where, record)
    assert shared.posts == 0
    assert not any((tmp_path / 'forge').iterdir())
    control = ResultStore(tmp_path / 'store2', shared)  # PLANTED: the same put, shareable, does reach it
    where, record = staged(control, '42')
    control.put(where, replace(record, local_only=False))
    assert shared.posts == 1


def held_rename(monkeypatch: pytest.MonkeyPatch, refusals: int) -> list[float]:
    """Plant a rename a foreign handle refuses *refusals* times (WinError 5), then lets through; return the waits."""
    real, calls, waits = Path.rename, [0], []

    def rename(self: Path, target: Path) -> Path:
        calls[0] += 1
        if calls[0] <= refusals:
            raise PermissionError(13, 'Access is denied', str(self), 5, str(target))
        return real(self, target)

    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(store_module.time, 'sleep', waits.append)
    return waits


def test_a_held_install_rename_is_retried_until_it_goes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResultStore(tmp_path / 'store')
    waits = held_rename(monkeypatch, refusals=3)
    put = store.put(*staged(store, '42'))
    assert put.output('out.txt').read_text(encoding='utf-8') == '42'
    assert waits == list(store_module.INSTALL_BACKOFF_S[:3])


def test_a_rename_held_past_every_retry_raises_naming_the_staging_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResultStore(tmp_path / 'store')
    waits = held_rename(monkeypatch, refusals=10**6)
    where, record = staged(store, '42')
    with pytest.raises(PermissionError, match=where.name):
        store.put(where, record)
    assert waits == list(store_module.INSTALL_BACKOFF_S)


def test_a_writer_that_wins_during_the_retry_wait_is_the_first_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResultStore(tmp_path / 'store')
    first = store.put(*staged(store, '42', host='h2'))
    held_rename(monkeypatch, refusals=1)
    assert store.put(*staged(store, '42')) == first
    with pytest.raises(Nondeterministic):
        store.put(*staged(store, '43'))


# --- file payloads, arrival verification, the size limit and tombstones ---------------------------------------


def record_for(*, local_only: bool = False, host: str = 'h1') -> Record:
    return Record(UNIT, 'vendor.solve', '1', 'env', host, 't0', 't1', '', local_only)


def test_files_round_trip_locally_from_bytes_and_paths(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    source = tmp_path / 'solved.ans'
    source.write_bytes(b'\x00solution')
    put = store.put_files({'solved.ans': source, 'value.json': b'{"t":1}'}, record_for())
    got = store.get(UNIT)
    assert isinstance(got, Result)
    assert got == put
    assert sorted(got.files) == ['solved.ans', 'value.json']
    assert got.files['solved.ans'].read_bytes() == b'\x00solution'
    assert got.files['solved.ans'].is_relative_to(tmp_path / 'store')
    assert got.record.result_hash == result_hash(got.path, ('solved.ans', 'value.json'))
    assert source.read_bytes() == b'\x00solution'  # a path payload is copied, never moved


def test_a_file_name_that_is_the_record_or_a_path_is_refused(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    for name in (RECORD, 'a/b.ans', '..', ''):
        with pytest.raises(ValueError, match='file name'):
            store.put_files({name: b'x'}, record_for())
    assert store.put_files({'ok.ans': b'x'}, record_for()).files['ok.ans'].read_bytes() == b'x'  # PLANTED


def test_the_same_key_with_different_file_bytes_is_nondeterministic(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    store.put_files({'solved.ans': b'one'}, record_for())
    assert store.put_files({'solved.ans': b'one'}, record_for(host='h2')).record.host == 'h1'  # PLANTED: equal
    with pytest.raises(Nondeterministic, match='the first stands'):
        store.put_files({'solved.ans': b'two'}, record_for(host='h2'))
    assert store.get(UNIT).files['solved.ans'].read_bytes() == b'one'


def test_a_shared_file_result_round_trips_and_a_sha_mismatch_on_arrival_is_refused(tmp_path: Path) -> None:
    tier = Shared(tmp_path / 'forge')
    ResultStore(tmp_path / 'a', tier).put_files({'solved.ans': b'payload'}, record_for())
    good = ResultStore(tmp_path / 'b', tier).get(UNIT)  # PLANTED: the clean arrival installs
    assert isinstance(good, Result)
    assert good.files['solved.ans'].read_bytes() == b'payload'
    tier.corrupt = True
    there = ResultStore(tmp_path / 'c', tier)
    with pytest.raises(ArrivalMismatch, match=UNIT[:16]):
        there.get(UNIT)
    assert not (tmp_path / 'c' / UNIT[:2] / UNIT).exists()  # nothing corrupt was installed
    assert not any((tmp_path / 'c' / '.staging').iterdir())


def test_a_payload_over_the_tier_limit_is_refused_by_name_and_kept_locally(tmp_path: Path) -> None:
    tier = Shared(tmp_path / 'forge', max_bytes=1000)
    store = ResultStore(tmp_path / 'store', tier)
    with pytest.raises(TooLarge, match='over the shared tier limit of 1000 bytes') as refused:
        store.put_files({'solved.ans': b'9' * 1001}, record_for())
    assert tier.posts == 0
    assert refused.value.result.files['solved.ans'].read_bytes() == b'9' * 1001  # whole, never truncated
    small = ResultStore(tmp_path / 'small', tier)  # PLANTED: under the limit it travels
    small.put_files({'solved.ans': b'8' * 8}, replace(record_for(), unit_hash='cd' + 'e' * 62))
    assert tier.posts == 1


def test_a_dirty_tree_file_result_never_uploads(tmp_path: Path) -> None:
    tier = Shared(tmp_path / 'forge')
    ResultStore(tmp_path / 'a', tier).put_files({'solved.ans': b'x'}, record_for(local_only=True))
    assert tier.posts == 0
    assert not any(tier.root.iterdir())
    ResultStore(tmp_path / 'b', tier).put_files({'solved.ans': b'x'}, record_for())  # PLANTED: clean uploads
    assert tier.posts == 1


def test_a_tombstone_makes_a_get_miss_with_its_reason_and_a_reput_records(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'store')
    first = store.put_files({'solved.ans': b'old'}, record_for())
    tomb = store.invalidate(UNIT, 'mesh was too coarse', host='h1')
    assert tomb.result_hash == first.record.result_hash
    got = store.get(UNIT)
    assert isinstance(got, Tombstone)
    assert got.reason == 'mesh was too coarse'
    again = store.put_files({'solved.ans': b'new'}, record_for())  # differs, but after a tombstone: no alarm
    assert store.get(UNIT) == again
    assert again.files['solved.ans'].read_bytes() == b'new'
    history = store.history(UNIT)
    assert [t.reason for t, _ in history] == ['mesh was too coarse']
    assert history[0][1].files['solved.ans'].read_bytes() == b'old'  # nothing deleted


def test_a_tombstone_on_one_host_is_seen_on_another(tmp_path: Path) -> None:
    tier = Shared(tmp_path / 'forge')
    a, b = ResultStore(tmp_path / 'a', tier), ResultStore(tmp_path / 'b', tier)
    a.put_files({'solved.ans': b'old'}, record_for())
    assert isinstance(b.get(UNIT), Result)  # b holds it locally now
    a.invalidate(UNIT, 'wrong material', host='h1')
    seen = b.get(UNIT)
    assert isinstance(seen, Tombstone)
    assert seen.reason == 'wrong material'
    b.put_files({'solved.ans': b'new'}, record_for(host='h2'))  # generation 1 on the tier: no alarm
    fresh = ResultStore(tmp_path / 'c', tier).get(UNIT)
    assert isinstance(fresh, Result)
    assert fresh.files['solved.ans'].read_bytes() == b'new'
