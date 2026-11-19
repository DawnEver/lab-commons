"""L2: a result over the shared tier's limit travels as Refs to a large-object store, verified by sha256 on arrival."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest
from test_compute_store import Shared

from lab_commons.compute.store import REF_SUFFIX, ArrivalMismatch, Record, Ref, Result, ResultStore, TooLarge

UNIT = 'cd' + 'f' * 62
BIG = b'\x01' * 5000
SMALL = b'{"t":1}'


class Objects:
    """A large-object store in a directory; *corrupt* flips the bytes it hands back."""

    def __init__(self, root: Path) -> None:
        """Objects kept under *root*."""
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.corrupt = root, False

    def put(self, path: Path) -> Ref:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copyfile(path, self.root / digest)
        return Ref('dir', digest, path.stat().st_size, digest)

    def get(self, ref: Ref, dest: Path) -> Path:
        data = (self.root / ref.ref).read_bytes()
        dest.write_bytes(data + b'!' if self.corrupt else data)
        return dest


def record_for() -> Record:
    return Record(UNIT, 'ours.solve', 'v', 'env', 'h1', 't0', 't1', '', local_only=False)


def stores(tmp_path: Path) -> tuple[ResultStore, ResultStore, Shared, Objects]:
    shared, objects = Shared(tmp_path / 'shared', max_bytes=1000), Objects(tmp_path / 'objects')
    one = ResultStore(tmp_path / 'one', shared, objects=objects)
    two = ResultStore(tmp_path / 'two', shared, objects=objects)
    return one, two, shared, objects


def test_a_large_file_travels_as_a_ref_and_arrives_whole(tmp_path: Path) -> None:
    one, two, shared, _ = stores(tmp_path)
    put = one.put_files({'solved.ans': BIG, 'value.json': SMALL}, record_for())
    assert put.files['solved.ans'].read_bytes() == BIG  # the local tier keeps the bytes
    posted = next(shared.root.glob(f'{UNIT}-g0'))
    assert sorted(one.name for one in posted.iterdir()) == ['record.json', f'solved.ans{REF_SUFFIX}', 'value.json']
    got = two.get(UNIT)
    assert isinstance(got, Result)
    assert got.files['solved.ans'].read_bytes() == BIG
    assert got.files['value.json'].read_bytes() == SMALL
    assert got.record.result_hash == put.record.result_hash


def test_a_ref_whose_bytes_differ_on_arrival_is_a_miss_never_a_value(tmp_path: Path) -> None:
    """PLANTED: the object store hands back other bytes; nothing installs."""
    one, two, _, objects = stores(tmp_path)
    one.put_files({'solved.ans': BIG}, record_for())
    objects.corrupt = True
    with pytest.raises(ArrivalMismatch, match=r"'solved.ans'"):
        two.get(UNIT)
    assert not two.path(UNIT).exists()


def test_without_a_large_object_store_an_oversize_result_is_still_too_large(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'one', Shared(tmp_path / 'shared', max_bytes=1000))
    with pytest.raises(TooLarge):
        store.put_files({'solved.ans': BIG}, record_for())


def test_a_ref_arriving_where_no_large_object_store_was_given_is_refused_by_name(tmp_path: Path) -> None:
    one, _, shared, _ = stores(tmp_path)
    one.put_files({'solved.ans': BIG}, record_for())
    with pytest.raises(ArrivalMismatch, match='no large-object store'):
        ResultStore(tmp_path / 'three', shared).get(UNIT)


def test_a_payload_name_with_the_ref_suffix_is_refused(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / 'one')
    with pytest.raises(ValueError, match=re.escape(REF_SUFFIX)):
        store.put_files({f'x{REF_SUFFIX}': SMALL}, record_for())
