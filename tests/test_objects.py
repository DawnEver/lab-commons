"""``lab_commons.objects``: the content-addressed object store on an rclone remote, sha256-checked both ways."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lab_commons import home, hostenv
from lab_commons.compute.store import (
    RECORD,
    REF_SUFFIX,
    ArrivalMismatch,
    Nondeterministic,
    Record,
    Result,
    ResultStore,
    Tombstone,
)
from lab_commons.objects import (
    OBJECTS_PREFIX,
    RCLONE_CONFIG,
    ConfigUnrendered,
    NotLocked,
    ObjectRefused,
    RcloneAbsent,
    RcloneObjects,
    UploadUnverified,
    declared_objects,
    rclone_exe,
)
from lab_commons.storage import Storage, declared

UNIT = 'ab' + 'e' * 62
BIG = b'\x02' * 6000
SMALL = b'{"v":1}'

#: A stand-in rclone over a directory. FAKE_RCLONE_CORRUPT makes every upload land other bytes of the same size.
_FAKE_RCLONE = """
import json, os, shutil, sys
from pathlib import Path
base = Path(os.environ['FAKE_RCLONE_ROOT'])
argv = sys.argv[1:]
at = next(i for i, a in enumerate(argv) if a in {'lsjson', 'copyto', 'deletefile'})
args = [a for a in argv[at:] if not a.startswith('-')]
flags = [a for a in argv if a.startswith('--')]
with open(base / 'calls.jsonl', 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
def local(spec):
    remote, _, path = spec.partition(':')
    target = (base / remote / path).resolve()
    if not target.is_relative_to(base.resolve()):
        sys.stderr.write(f'fake rclone: {spec} resolves outside the fake remote root'); sys.exit(5)
    return target
verb = args[0]
if verb == 'lsjson':
    root = local(args[1])
    if not root.is_dir():
        sys.stderr.write('directory not found'); sys.exit(3)
    print(json.dumps([{'Path': p.relative_to(root).as_posix(), 'Size': p.stat().st_size}
                      for p in sorted(root.rglob('*')) if p.is_file()]))
elif verb == 'copyto':
    src, dst = args[1], args[2]
    if ':' in src[2:]:
        Path(dst).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(local(src), dst)
    else:
        target = local(dst)
        if '--immutable' in flags and target.exists():
            sys.stderr.write('immutable file modified'); sys.exit(1)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = Path(src).read_bytes()
        target.write_bytes(bytes(len(data)) if os.environ.get('FAKE_RCLONE_CORRUPT') else data)
elif verb == 'deletefile':
    local(args[1]).unlink()
else:
    sys.exit(2)
"""


@pytest.fixture
def cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[tuple[str, ...], Path]:
    """A fake rclone over a directory standing in for the remote."""
    root = tmp_path / 'cloud'
    root.mkdir()
    script = tmp_path / 'rclone.py'
    script.write_text(textwrap.dedent(_FAKE_RCLONE), encoding='utf-8')
    monkeypatch.setenv('FAKE_RCLONE_ROOT', str(root))
    monkeypatch.delenv('FAKE_RCLONE_CORRUPT', raising=False)
    return (sys.executable, str(script)), root


def _big(tmp_path: Path) -> Path:
    big = tmp_path / 'big.bin'
    big.write_bytes(BIG)
    return big


def _uploads(root: Path) -> int:
    calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
    return sum(1 for call in calls if call[0] == 'copyto' and '--immutable' in call)


@pytest.mark.parametrize(('spec', 'refused'), [('bucket:/results/d6/x', True), ('bucket:results/d6/x', False)])
def test_the_fake_remote_never_writes_off_its_root(
    tmp_path: Path, cloud: tuple[tuple[str, ...], Path], spec: str, *, refused: bool
) -> None:
    """A rooted remote path joined onto the fake's dir is the drive root: refused (PLANTED: the relative one lands)."""
    rclone, root = cloud
    source = tmp_path / 'one.bin'
    source.write_bytes(b'1')
    run = subprocess.run([*rclone, 'copyto', str(source), spec], capture_output=True, text=True, check=False)
    assert (run.returncode == 5) is refused, run.stderr
    assert (root / 'bucket' / 'results' / 'd6' / 'x').is_file() is not refused


def test_a_file_goes_up_once_and_comes_back_whole(tmp_path: Path, cloud: tuple[tuple[str, ...], Path]) -> None:
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    assert ref.bytes == len(BIG)
    assert ref.ref == objects.key(ref.sha256) == f'results/{ref.sha256[:2]}/{ref.sha256}'
    assert objects.put(_big(tmp_path)) == ref, 'the same bytes again is the object already there'
    assert _uploads(root) == 1
    assert objects.get(ref, tmp_path / 'back' / 'big.bin').read_bytes() == BIG


def test_an_upload_is_verified_after_it_lands(
    tmp_path: Path, cloud: tuple[tuple[str, ...], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED CONTROL: the remote keeps other bytes of the same size; the upload is refused and the object removed."""
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    monkeypatch.setenv('FAKE_RCLONE_CORRUPT', '1')
    with pytest.raises(UploadUnverified, match='re-hashed'):
        objects.put(_big(tmp_path))
    assert not [p for p in (root / 'bucket').rglob('*') if p.is_file()], 'a bad object never stays to be found'
    monkeypatch.delenv('FAKE_RCLONE_CORRUPT')
    ref = objects.put(_big(tmp_path))
    objects.verify(ref)


def test_verify_refuses_a_stored_object_that_no_longer_hashes(
    tmp_path: Path, cloud: tuple[tuple[str, ...], Path]
) -> None:
    """PLANTED CONTROL: verify passes the landed object and refuses it once tampered."""
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    objects.verify(ref)
    (root / 'bucket' / ref.ref).write_bytes(BIG + b'!')
    with pytest.raises(ArrivalMismatch, match='arrived hashing'):
        objects.verify(ref)


def test_a_tampered_object_is_a_miss_never_a_value(tmp_path: Path, cloud: tuple[tuple[str, ...], Path]) -> None:
    """PLANTED CONTROL: the remote hands back other bytes; nothing is left at the destination."""
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    (root / 'bucket' / ref.ref).write_bytes(BIG + b'!')
    dest = tmp_path / 'back' / 'big.bin'
    with pytest.raises(ArrivalMismatch, match='arrived hashing'):
        objects.get(ref, dest)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == [], 'no partial download is left beside it either'


def test_fetch_by_a_locked_sha_arrives_whole(tmp_path: Path, cloud: tuple[tuple[str, ...], Path]) -> None:
    rclone, _root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    got = objects.fetch(ref.sha256, tmp_path / 'cache' / 'big.bin', lock={ref.sha256})
    assert got.read_bytes() == BIG


def test_fetch_of_a_sha_the_lock_does_not_name_is_refused(tmp_path: Path, cloud: tuple[tuple[str, ...], Path]) -> None:
    """PLANTED CONTROL: the object IS stored, but a lock not naming it is refused before anything is fetched."""
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    calls = len((root / 'calls.jsonl').read_text().splitlines())
    dest = tmp_path / 'cache' / 'big.bin'
    with pytest.raises(NotLocked, match=ref.sha256[:16]):
        objects.fetch(ref.sha256, dest, lock={'0' * 64})
    assert not dest.exists()
    assert len((root / 'calls.jsonl').read_text().splitlines()) == calls, 'the remote is never asked'


def test_fetch_whose_bytes_mismatch_the_locked_sha_leaves_no_file(
    tmp_path: Path, cloud: tuple[tuple[str, ...], Path]
) -> None:
    """PLANTED CONTROL: the stored object no longer hashes to the locked sha: refused, no file at or beside *dest*."""
    rclone, root = cloud
    objects = RcloneObjects('bucket:', 'results', rclone)
    ref = objects.put(_big(tmp_path))
    (root / 'bucket' / ref.ref).write_bytes(b'tampered')
    dest = tmp_path / 'cache' / 'big.bin'
    with pytest.raises(ArrivalMismatch, match='arrived hashing'):
        objects.fetch(ref.sha256, dest, lock={ref.sha256})
    assert not dest.parent.exists() or list(dest.parent.iterdir()) == []


@pytest.mark.parametrize('sha', ['ABC', 'g' * 64, 'a' * 63])
def test_a_malformed_sha_is_refused(sha: str) -> None:
    with pytest.raises(ObjectRefused, match='sha256'):
        RcloneObjects('bucket:', 'results', ('rclone',)).key(sha)


@pytest.mark.parametrize(
    ('prefix', 'refused'),
    [('scratch/results', 'scratch'), ('a//b', 'empty segment'), ('rés', 'ASCII'), ('a:b', 'character'),
     ('vendor/scratch-free', '')],
)  # fmt: skip
def test_a_prefix_a_bucket_ages_out_or_cannot_hold_is_refused(prefix: str, refused: str) -> None:
    """The bucket ages scratch/ out, so no durable object is keyed there; PLANTED: a durable prefix is admitted."""
    if not refused:
        assert RcloneObjects('bucket:', prefix, ('rclone',)).prefix == prefix
        return
    with pytest.raises(ObjectRefused, match=refused):
        RcloneObjects('bucket:', prefix, ('rclone',))


def test_no_rclone_named_is_refused_with_its_door() -> None:
    """The pinned rclone is the caller's to name: an empty argv is never a PATH guess."""
    with pytest.raises(ObjectRefused, match='rclone'):
        RcloneObjects('bucket:', 'results', ())


def test_a_bare_remote_takes_the_key_relative_never_from_the_root() -> None:
    assert RcloneObjects('bucket:', 'r', ('rclone',)).at('results/d6/x') == 'bucket:results/d6/x'
    assert RcloneObjects('drive:base/', 'r', ('rclone',)).at('k') == 'drive:base/k'


def _family(*, runs: str | None = 'bucket:vault') -> Storage:
    remote = {'type': 's3', 'secret_keys': ['secret_access_key']}
    return declared({}, {'storage': {'remotes': {'bucket': remote}, **({'runs': runs} if runs else {})}})


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user home of its own: the pinned bin directory and the rendered config both resolve under *tmp_path*."""
    for name in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(name, str(tmp_path / 'user'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    return tmp_path


def _placed(*, rclone: bool = True, config: bool = True) -> tuple[Path, Path]:
    exe = home.pinned_bin() / ('rclone.exe' if sys.platform == 'win32' else 'rclone')
    conf = RCLONE_CONFIG.expanduser()
    for there, wanted in ((exe, rclone), (conf, config)):
        if wanted:
            there.parent.mkdir(parents=True, exist_ok=True)
            there.write_bytes(b'')
    return exe, conf


@pytest.mark.parametrize(
    ('windows', 'environ', 'expected'),
    [(False, {'HOME': '/h/u'}, Path('/h/u/.local/bin')), (True, {'LOCALAPPDATA': 'C:/L'}, Path('C:/L/dev-lab/bin'))],
)
def test_the_pinned_bin_is_user_space_per_os(windows: bool, environ: dict[str, str], expected: Path) -> None:  # noqa: FBT001
    assert home.pinned_bin(windows=windows, host=hostenv.context(environ)) == expected


def test_the_declared_run_store_resolves_the_pinned_rclone_and_the_rendered_config(machine: Path) -> None:
    """No argument: the product reads both from the machine, never from dev-lab and never from PATH."""
    exe, conf = _placed()
    objects = declared_objects(_family())
    assert objects is not None
    assert (objects.remote, objects.prefix) == ('bucket:vault', OBJECTS_PREFIX)
    assert objects.rclone == (str(exe), f'--config={conf}'), 'every call reads the rendered config, never a default'
    assert conf.is_relative_to(machine)


def test_an_absent_pinned_rclone_is_refused_with_its_door_even_on_path(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED CONTROL: an rclone on PATH is not the pinned one, so it is still refused by name."""
    _placed(rclone=False)
    stray = machine / 'path'
    stray.mkdir()
    (stray / ('rclone.exe' if sys.platform == 'win32' else 'rclone')).write_bytes(b'')
    monkeypatch.setenv('PATH', str(stray))
    with pytest.raises(RcloneAbsent, match=r'toolrelease install rclone'):
        declared_objects(_family())
    with pytest.raises(RcloneAbsent):
        rclone_exe()


def test_an_unrendered_config_is_refused_with_its_door(machine: Path) -> None:
    """PLANTED CONTROL: the pinned rclone is there, the rendered config is not."""
    del machine
    _placed(config=False)
    with pytest.raises(ConfigUnrendered, match=r'storage render'):
        declared_objects(_family())


def test_explicit_rclone_and_config_are_the_caller_s(tmp_path: Path) -> None:
    """dev-lab's reach door renders to its own ``--to`` and names its rclone: both pass through unchecked."""
    conf = tmp_path / 'c.conf'
    objects = declared_objects(_family(), rclone=('fake',), config=conf)
    assert objects is not None
    assert objects.rclone == ('fake', f'--config={conf}')


def test_an_undeclared_run_store_is_none_and_resolves_nothing(machine: Path) -> None:
    """PLANTED CONTROL: no ``[storage] runs`` -- ``None`` with no rclone and no config placed (no refusal)."""
    del machine
    assert declared_objects(_family(runs=None)) is None


class Shared:
    """A shared tier in a directory: first post per generation wins."""

    def __init__(self, root: Path) -> None:
        """A tier under *root*."""
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.max_bytes = root, 1000

    def fetch(self, unit_hash: str, generation: int, dest: Path) -> Path | None:
        """The posted result, copied to *dest*."""
        held = self.root / f'{unit_hash}-g{generation}'
        if not held.is_dir():
            return None
        shutil.copytree(held, dest, dirs_exist_ok=True)
        return dest

    def post(self, result: Result, generation: int) -> None:
        """Keep *result*; a different one for the generation is nondeterminism."""
        held = self.root / f'{result.record.unit_hash}-g{generation}'
        if held.is_dir():
            if Record.read(held / RECORD).result_hash != result.record.result_hash:
                raise Nondeterministic(result.record.unit_hash)
            return
        shutil.copytree(result.path, held)

    def bury(self, tombstone: Tombstone) -> None:
        """No graves in these tests."""

    def graves(self, unit_hash: str) -> tuple[Tombstone, ...]:
        """None."""
        del unit_hash
        return ()


def test_an_oversize_result_travels_by_ref_and_arrives_whole(
    tmp_path: Path, cloud: tuple[tuple[str, ...], Path]
) -> None:
    """The store is the ResultStore's LargeObjects: by Ref up, re-hashed down, a tampered object never installed."""
    rclone, root = cloud
    shared = Shared(tmp_path / 'shared')
    objects = RcloneObjects('bucket:', 'results', rclone)
    record = Record(UNIT, 'ours.solve', 'v', 'env', 'h1', 't0', 't1', '', local_only=False)
    ResultStore(tmp_path / 'one', shared, objects=objects).put_files({'solved.ans': BIG, 'value.json': SMALL}, record)
    posted = next(shared.root.glob(f'{UNIT}-g0'))
    assert (posted / f'solved.ans{REF_SUFFIX}').is_file()
    got = ResultStore(tmp_path / 'two', shared, objects=objects).get(UNIT)
    assert isinstance(got, Result)
    assert got.files['solved.ans'].read_bytes() == BIG
    ref = json.loads((posted / f'solved.ans{REF_SUFFIX}').read_text())
    (root / 'bucket' / ref['ref']).write_bytes(b'tampered')
    three = ResultStore(tmp_path / 'three', shared, objects=objects)
    with pytest.raises(ArrivalMismatch):
        three.get(UNIT)
    assert not three.path(UNIT).exists()
