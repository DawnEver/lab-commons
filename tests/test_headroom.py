"""The cache floor holds against a declared account QUOTA, not only the volume (D54): a shared volume lies."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from lab_commons import storage
from lab_commons.compute import floor
from lab_commons.headroom import Floor, Quota, QuotaUnreadable, Usage, read
from lab_commons.storage import StorageError, declared

TB = 1024**4
GPFS = Quota(('mmlsquota', '--block-size', '1', 'home'), 'mmlsquota')
LINUX = Quota(('quota', '-w'), 'quota-w')

MMLSQUOTA = """\
                         Block Limits                                    |     File Limits
Filesystem type   blocks   quota   limit in_doubt grace | files  quota  limit in_doubt grace Remarks
home       USR         {used}  {quota}  {limit}          0     none |  {files}  2000000  2000000        0     none
"""

QUOTA_W = """\
Disk quotas for user someone (uid 1000):
     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace
      /dev/sda1  {used}*  {quota}  {limit}  6days   {files}       0       0
"""


Run = Callable[..., subprocess.CompletedProcess[str]]


def ran(stdout: str, code: int = 0) -> Run:
    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, stdout, 'boom' if code else '')

    return run


def gpfs(used: int, quota: int, limit: int, files: int = 10) -> str:
    return MMLSQUOTA.format(used=used, quota=quota, limit=limit, files=files)


def test_mmlsquota_reads_bytes_left_in_its_block_size_and_files_left() -> None:
    usage = read(GPFS, ran(gpfs(used=900, quota=1000, limit=1200, files=1_999_000)))
    assert usage == Usage(bytes_left=100, files_left=1000, files_limit=2_000_000)


def test_mmlsquota_without_a_block_size_counts_kib() -> None:
    usage = read(Quota(('mmlsquota', 'home'), 'mmlsquota'), ran(gpfs(used=1, quota=3, limit=0)))
    assert usage.bytes_left == 2 * 1024


def test_quota_w_reads_kib_and_treats_zero_as_no_limit() -> None:
    usage = read(LINUX, ran(QUOTA_W.format(used=500, quota=1000, limit=2000, files=7)))
    assert usage == Usage(bytes_left=500 * 1024, files_left=None, files_limit=None)


def test_quota_w_with_no_quota_is_unlimited() -> None:
    assert read(LINUX, ran('Disk quotas for user someone (uid 1000): none\n')) == Usage(None, None, None)


@pytest.mark.parametrize(
    ('quota', 'run', 'cause'),
    [
        (GPFS, ran('', code=1), 'exited 1'),
        (GPFS, ran('nothing it knows\n'), 'printed 0 filesystem rows'),
        (GPFS, ran(gpfs(1, 2, 3) + gpfs(1, 2, 3).splitlines()[-1] + '\n'), 'printed 2 filesystem rows'),
        (Quota(('mmlsquota', '--block-size', 'auto'), 'mmlsquota'), ran(gpfs(1, 2, 3)), 'suffixed counts'),
        (Quota(('quota', '-w', '-s'), 'quota-w'), ran(''), 'human-readable'),
        (Quota(('x',), 'df'), ran(''), 'format is not one of'),
    ],
)
def test_an_unreadable_declared_quota_is_refused_by_name(quota: Quota, run: Run, cause: str) -> None:
    with pytest.raises(QuotaUnreadable, match=cause):
        read(quota, run)


def test_a_probe_that_cannot_run_is_refused_by_name() -> None:
    def missing(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    with pytest.raises(QuotaUnreadable, match='could not run'):
        read(GPFS, missing)


class Account:
    """A huge shared volume with a quota: the quota's headroom shrinks by what the cache holds."""

    def __init__(self, limit: int, files: int = 10) -> None:
        """An account of *limit* bytes holding *files* files."""
        self.limit, self.files, self.held = limit, files, 0

    def usage(self, _quota: Quota) -> Usage:
        return Usage(self.limit - self.held, 2_000_000 - self.files, 2_000_000)


def wire(monkeypatch: pytest.MonkeyPatch, account: Account) -> None:
    monkeypatch.setattr(floor, 'free_bytes', lambda _v: 500 * TB)
    monkeypatch.setattr(floor, 'read', account.usage)


def entries(account: Account, sizes: list[int]) -> tuple[list[tuple[int, Path, int]], list[Path]]:
    live = [(n, Path(f'e{n}'), size) for n, size in enumerate(sizes)]
    account.held = sum(sizes)
    return live, []


def test_a_huge_volume_with_a_nearly_full_quota_still_evicts(monkeypatch: pytest.MonkeyPatch) -> None:
    account = Account(limit=1000)
    wire(monkeypatch, account)
    live, gone = entries(account, [300, 300, 300])

    def evict(path: Path) -> None:
        gone.append(path)
        account.held -= 300

    floor.keep(Path('v'), live, Floor(400, quota=GPFS), evict=evict)
    assert gone == [Path('e0')]  # 900 held -> 600: the quota keeps 400 free again


def test_a_roomy_quota_evicts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    account = Account(limit=100_000)
    wire(monkeypatch, account)
    live, gone = entries(account, [300, 300, 300])
    floor.keep(Path('v'), live, Floor(400, quota=GPFS), evict=gone.append)
    assert gone == []


def test_a_file_limited_quota_keeps_one_percent_of_its_files_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    account = Account(limit=100_000, files=1_990_000)  # 10_000 files left < 1% (20_000)
    wire(monkeypatch, account)
    live, _ = entries(account, [1])
    with pytest.raises(floor.BelowFloor, match='keeps 20000 files free but has 10000 left'):
        floor.keep(Path('v'), live, Floor(1, quota=GPFS), evict=lambda _p: None, keep=Path('e0'))


def test_a_declared_files_floor_replaces_the_one_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    account = Account(limit=100_000, files=1_990_000)
    wire(monkeypatch, account)
    live, gone = entries(account, [1])
    floor.keep(Path('v'), live, Floor(1, files=5_000, quota=GPFS), evict=gone.append)
    assert gone == []


def test_a_floor_without_a_quota_never_runs_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(floor, 'free_bytes', lambda _v: 500 * TB)

    def probe(_quota: Quota) -> Usage:
        pytest.fail('no quota declared: nothing to probe')

    monkeypatch.setattr(floor, 'read', probe)
    assert floor.keep(Path('v'), [(0, Path('e0'), 1)], Floor(1), evict=lambda _p: None) == []


def test_an_unreadable_quota_is_never_replaced_by_the_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(floor, 'free_bytes', lambda _v: 500 * TB)
    monkeypatch.setattr(floor, 'read', lambda q: read(q, ran('', code=2)))
    with pytest.raises(QuotaUnreadable, match='exited 2'):
        floor.keep(Path('v'), [(0, Path('e0'), 1)], Floor(1, quota=GPFS), evict=lambda _p: None)


def test_a_machine_declares_its_quota_and_files_floor() -> None:
    held = declared(
        {'storage': {'quota': {'probe': ['quota', '-w'], 'format': 'quota-w'}, 'free_floor_files': 5000}}, {}
    )
    assert storage.free_floor(held) == Floor(50 * 1024**3, 5000, LINUX)


@pytest.mark.parametrize(
    ('quota', 'match'),
    [
        ({'probe': [], 'format': 'quota-w'}, 'quota.probe'),
        ({'probe': ['quota'], 'format': 'df'}, 'quota.format'),
        ({'probe': ['quota'], 'format': 'quota-w', 'unit': 'K'}, 'quota.unit is not a quota key'),
    ],
)
def test_a_malformed_quota_is_refused_by_name(quota: dict, match: str) -> None:
    with pytest.raises(StorageError, match=match):
        declared({'storage': {'quota': quota}}, {})


@pytest.mark.parametrize('bad', [0, -1, 1.5, True])
def test_a_files_floor_that_is_not_a_positive_count_is_refused(bad: object) -> None:
    with pytest.raises(StorageError, match='free_floor_files'):
        declared({'storage': {'free_floor_files': bad}}, {})
