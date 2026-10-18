"""The OPT-IN archive of the worktree door, its retention, and the output file ceiling (2026-10-19).

Planted controls: an undeclared archive root refuses ``--archive``; a failed move keeps the tree; a
live tree and a locked tree are skipped untouched; an unreadable process table refuses; a ``.venv``
(at the top or nested inside ``output/``) never reaches the archive; a destination that exists
refuses; a young stamp is kept (its regenerable content deleted), an expired one removed, an
unparseable one listed and never removed; the ceiling count stops early. Planted success: an
ignored ``output/`` is moved, its HEAD recorded, and the tree removed WITHOUT ``--force``.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import lab_commons.dev.worktreearchive as wa
import lab_commons.dev.worktrees as wt
from lab_commons.dev.famtests.visibility import plant_checkout

_GIT = shutil.which('git') or 'git'
_TODAY = dt.date(2026, 10, 19)
_ARCHIVE = "archive = 'output/worktree-archive'\narchive_days = 14\n"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True, encoding='utf-8', timeout=60
    ).stdout


def _planted(tmp_path: Path, declared: str = _ARCHIVE) -> Path:
    """A main checkout with one pushed worktree ``lane`` holding an ignored ``output/`` and a ``.venv``."""
    work = plant_checkout(tmp_path, trunk='main').work
    (work / '.gitignore').write_text('output/\n.claude/\n.venv/\n', encoding='utf-8')
    (work / 'pyproject.toml').write_text(f'[tool.lab_commons.worktrees]\n{declared}', encoding='utf-8')
    _run(work, 'add', '.gitignore', 'pyproject.toml')
    _run(work, 'commit', '-q', '-m', 'ignore')
    _run(work, 'push', '-q', 'origin', 'main')
    lane = work / '.claude' / 'worktrees' / 'lane'
    _run(work, 'worktree', 'add', '--detach', str(lane), 'origin/main')
    (lane / 'output' / 'logs').mkdir(parents=True)
    (lane / 'output' / 'logs' / 'result.txt').write_text('keep me', encoding='utf-8')
    (lane / 'output' / 'copy' / '.venv' / 'Lib').mkdir(parents=True)
    (lane / 'output' / 'copy' / '.venv' / 'Lib' / 'x.py').write_text('', encoding='utf-8')
    (lane / '.venv' / 'Scripts').mkdir(parents=True)
    (lane / '.venv' / 'Scripts' / 'python.exe').write_text('', encoding='utf-8')
    return work


def _quiet() -> tuple[tuple[str, str], ...]:
    return (('python -m something_else', '/elsewhere'),)


def _archive(work: Path, processes: wa.Processes = _quiet) -> tuple[str, ...]:
    return wt.prune(work, apply=True, archive=True, today=_TODAY, processes=processes) or ()


def _dest(work: Path) -> Path:
    return work / 'output' / 'worktree-archive' / '20261019' / 'lane'


def test_the_default_still_never_moves_work(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    lines = wt.prune(work, apply=True) or ()
    assert (work / '.claude' / 'worktrees' / 'lane' / 'output' / 'logs' / 'result.txt').is_file()
    assert any(line.startswith('kept') and 'output' in line for line in lines)
    assert not (work / 'output').exists()


def test_archive_moves_ignored_work_records_head_and_removes_the_tree(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    lane = work / '.claude' / 'worktrees' / 'lane'
    head = _run(lane, 'rev-parse', 'HEAD').strip()
    lines = _archive(work)
    assert any(line.startswith('archived') for line in lines), lines
    assert not lane.exists()
    dest = _dest(work)
    assert (dest / 'output' / 'logs' / 'result.txt').read_text(encoding='utf-8') == 'keep me'
    assert head in (dest / wa.ARCHIVE_NOTE).read_text(encoding='utf-8')
    assert not (dest / '.venv').exists(), 'a .venv never reaches the archive'
    assert not (dest / 'output' / 'copy' / '.venv').exists(), 'nor one nested inside output/'
    assert (dest / 'output' / 'copy').is_dir()


def test_archive_without_prune_or_a_declared_root_refuses(tmp_path: Path) -> None:
    work = _planted(tmp_path, declared='')
    with pytest.raises(wa.ArchiveNotDeclared, match=r'\[tool\.lab_commons\.worktrees\] archive'):
        _archive(work)
    assert wt.main(['--root', str(work), '--prune', '--archive']) == 2
    with pytest.raises(SystemExit):
        wt.main(['--root', str(work), '--archive'])
    assert (work / '.claude' / 'worktrees' / 'lane' / 'output' / 'logs' / 'result.txt').is_file()


def test_archive_days_is_required_with_the_root(tmp_path: Path) -> None:
    work = _planted(tmp_path, declared="archive = 'output/worktree-archive'\n")
    with pytest.raises(wa.ArchiveNotDeclared, match='archive_days'):
        _archive(work)


def test_a_failed_move_keeps_the_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _planted(tmp_path)

    def broken(source: Path, target: Path) -> None:
        msg = f'planted: {source} -> {target}'
        raise OSError(msg)

    monkeypatch.setattr(wt, '_move', broken)
    lines = _archive(work)
    assert any(line.startswith('FAILED') for line in lines), lines
    lane = work / '.claude' / 'worktrees' / 'lane'
    assert (lane / 'output' / 'logs' / 'result.txt').read_text(encoding='utf-8') == 'keep me'
    assert lane in {Path(p) for p in _paths(work)}


def _paths(work: Path) -> list[str]:
    out = _run(work, 'worktree', 'list', '--porcelain')
    return [str(Path(line[len('worktree ') :])) for line in out.splitlines() if line.startswith('worktree ')]


def test_an_existing_destination_refuses(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    _dest(work).mkdir(parents=True)
    lines = _archive(work)
    assert any(line.startswith('FAILED') and 'exists' in line for line in lines), lines
    assert (work / '.claude' / 'worktrees' / 'lane' / 'output' / 'logs' / 'result.txt').is_file()


def test_a_live_tree_is_skipped_untouched(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    lane = work / '.claude' / 'worktrees' / 'lane'

    def busy() -> tuple[tuple[str, str], ...]:
        return ((f'python -m pytest {str(lane).upper()}', ''),)

    def sitting() -> tuple[tuple[str, str], ...]:
        return (('bash', str(lane / 'output')),)

    for processes in (busy, sitting):
        lines = _archive(work, processes=processes)
        assert any(line.startswith('kept') and 'live process' in line for line in lines), lines
        assert (lane / '.venv' / 'Scripts' / 'python.exe').is_file(), 'not even regenerable content goes'
        assert (lane / 'output' / 'logs' / 'result.txt').is_file()


def test_an_unreadable_process_table_refuses(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    lines = _archive(work, processes=lambda: None)
    assert any(line.startswith('kept') and 'liveness' in line for line in lines), lines
    assert (work / '.claude' / 'worktrees' / 'lane' / 'output').is_dir()


def test_a_locked_tree_is_skipped(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    lane = work / '.claude' / 'worktrees' / 'lane'
    _run(work, 'worktree', 'lock', str(lane))
    lines = _archive(work)
    assert any(line.startswith('kept') and 'locked' in line for line in lines), lines
    assert (lane / 'output' / 'logs' / 'result.txt').is_file()


def _stamps(work: Path) -> Path:
    root = work / 'output' / 'worktree-archive'
    for stamp in ('20261018', '20260901-103041', 'not-a-date', '20261399'):
        (root / stamp / 'tree' / 'output').mkdir(parents=True)
        (root / stamp / 'tree' / 'output' / 'r.txt').write_text('x', encoding='utf-8')
    (root / '20261018' / 'tree' / 'untracked' / '.venv').mkdir(parents=True)
    return root


def test_expire_is_dry_by_default_and_judged_by_the_stamp_name(tmp_path: Path) -> None:
    work = _planted(tmp_path)
    root = _stamps(work)
    old = (root / '20261018').stat().st_mtime - 400 * 86400
    os.utime(root / '20261018', (old, old))  # mtime says ancient; the NAME says young
    lines = wt.expire(work, apply=False, today=_TODAY)
    assert any(line.startswith('expired') and '20260901-103041' in line for line in lines), lines
    assert all((root / s).is_dir() for s in ('20261018', '20260901-103041', 'not-a-date', '20261399'))
    lines = wt.expire(work, apply=True, today=_TODAY)
    assert not (root / '20260901-103041').exists(), 'an expired stamp is removed'
    assert (root / '20261018' / 'tree' / 'output' / 'r.txt').is_file(), 'a young stamp is kept'
    assert not (root / '20261018' / 'tree' / 'untracked' / '.venv').exists(), 'its regenerable paths go at once'
    for bad in ('not-a-date', '20261399'):
        assert (root / bad / 'tree' / 'output' / 'r.txt').is_file(), f'{bad} is never deleted'
        assert any(line.startswith('unparsed') and bad in line for line in lines), lines


def test_expire_refuses_an_undeclared_root(tmp_path: Path) -> None:
    work = _planted(tmp_path, declared='')
    with pytest.raises(wa.ArchiveNotDeclared):
        wt.expire(work, apply=False, today=_TODAY)


def test_the_ceiling_count_stops_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for d in range(20):
        (tmp_path / f'd{d}').mkdir()
        for f in range(5):
            (tmp_path / f'd{d}' / f'{f}.txt').write_text('', encoding='utf-8')
    opened: list[str] = []
    real = os.scandir

    def spy(path: str | os.PathLike[str]) -> object:
        opened.append(str(path))
        return real(path)

    monkeypatch.setattr(wa.os, 'scandir', spy)
    assert wa.count_files((tmp_path,), limit=7) == 7
    assert len(opened) <= 3, f'the walk opened {len(opened)} directories for a limit of 7'
    opened.clear()
    assert wa.count_files((tmp_path, tmp_path / 'absent'), limit=10_000) == 100


def test_the_census_warns_at_the_declared_ceiling(tmp_path: Path) -> None:
    work = _planted(tmp_path, declared=_ARCHIVE + "file_ceiling = 3\nceiling_roots = ['output']\n")
    (work / 'output').mkdir()
    for f in range(5):
        (work / 'output' / f'{f}.txt').write_text('', encoding='utf-8')
    warning = wt.ceiling(work)
    assert warning is not None
    assert '--expire-archive' in warning
    assert 'output' in warning
    (work / 'pyproject.toml').write_text(
        "[tool.lab_commons.worktrees]\nfile_ceiling = 100\nceiling_roots = ['output']\n", encoding='utf-8'
    )
    assert wt.ceiling(work) is None
    (work / 'pyproject.toml').write_text('[tool.lab_commons.worktrees]\nfile_ceiling = 100\n', encoding='utf-8')
    with pytest.raises(ValueError, match='ceiling_roots'):
        wt.ceiling(work)
