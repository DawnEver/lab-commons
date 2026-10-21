"""D16/D18/D19: results are content-addressed, installed atomically, first-wins, and a dirty one stays home."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from lab_commons.compute import store as store_module
from lab_commons.compute.store import RECORD, Nondeterministic, Record, Result, ResultStore, result_hash

UNIT = 'ab' + 'c' * 62


class Shared:
    """A shared tier in a directory: first post wins, a differing one raises."""

    def __init__(self, root: Path) -> None:
        """A tier kept under *root*, counting posts."""
        self.root, self.posts = root, 0

    def fetch(self, unit_hash: str, dest: Path) -> Path | None:
        held = self.root / unit_hash
        if not held.is_dir():
            return None
        shutil.copytree(held, dest, dirs_exist_ok=True)
        return dest

    def post(self, result: Result) -> None:
        self.posts += 1
        held = self.root / result.record.unit_hash
        if held.is_dir():
            if Record.read(held / RECORD).result_hash != result.record.result_hash:
                raise Nondeterministic(result.record.unit_hash)
            return
        shutil.copytree(result.path, held)


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
    (tmp_path / 'forge').mkdir()
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
    (tmp_path / 'forge').mkdir()
    ResultStore(tmp_path / 'a', shared).put(*staged(ResultStore(tmp_path / 'a'), '42'))
    b = ResultStore(tmp_path / 'b', shared)
    with pytest.raises(Nondeterministic):
        b.put(*staged(b, '43'))


def test_a_local_only_result_never_leaves_the_box(tmp_path: Path) -> None:
    shared = Shared(tmp_path / 'forge')
    (tmp_path / 'forge').mkdir()
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
