"""THE SCHEDULED LOCK UPGRADE (D20): due daily or when a pinned source moved; the producer commits and pushes.

Driven on a REAL planted repository with a REAL bare origin; ``uv`` is the one seam (``relock``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from lab_commons.dev.lockcheck import TRAILER, upgraded_on
from lab_commons.dev.lockupgrade import PRODUCER, due, main, moved_sources, schedule, serve_tick

_GIT = shutil.which('git') or 'git'
_PIN = 'a' * 40
_SOURCE = f'source = {{ git = "https://f.invalid/kit.git#{_PIN}" }}\n'
_LOCK = f'version = 1\n\n[[package]]\nname = "kit"\nversion = "1"\n{_SOURCE}'
_MANIFEST = f"""[project]
name = "planted"
version = "0"

[tool.lab_commons.branchset]
trunk = "main"
sessions = ["integrate/main"]
update = "update/lock"

[tool.lab_commons.requests.{PRODUCER}]
argv = ["-m", "lab_commons.dev.lockupgrade"]
needs = []
cpu = 1
mem_gb = 2
wall_h = 1
"""


def _git(root: Path, *argv: str, when: str = '2026-10-15T12:00:00Z') -> str:
    env = {**os.environ, 'GIT_COMMITTER_DATE': when, 'GIT_AUTHOR_DATE': when}
    done = subprocess.run(
        [_GIT, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null', *argv],
        cwd=root, env=env, check=True, capture_output=True, text=True, encoding='utf-8', timeout=60,
    )  # fmt: skip
    return done.stdout.strip()


def _planted(tmp_path: Path, *, message: str = 'c') -> tuple[Path, str]:
    """A checkout on integrate/main with a lock and the declared producer, pushed to a bare origin."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    origin, root = tmp_path / 'origin.git', tmp_path / 'r'
    _git(tmp_path, 'init', '-q', '--bare', str(origin))
    root.mkdir()
    _git(root, 'init', '-q', '-b', 'integrate/main')
    (root / 'pyproject.toml').write_text(_MANIFEST, encoding='utf-8')
    (root / 'uv.lock').write_text(_LOCK, encoding='utf-8')
    _git(root, 'add', 'pyproject.toml', 'uv.lock')
    _git(root, 'commit', '-q', '-m', message)
    _git(root, 'remote', 'add', 'origin', str(origin))
    _git(root, 'push', '-q', 'origin', 'integrate/main')
    _git(root, 'fetch', '-q', 'origin')
    return root, _git(root, 'rev-parse', 'HEAD')


def test_moved_sources_names_a_git_source_whose_remote_tip_is_not_its_pin() -> None:
    assert moved_sources(_LOCK, tip=lambda _url: _PIN) == ()
    assert moved_sources(_LOCK, tip=lambda _url: 'b' * 40) == ('https://f.invalid/kit.git',), 'PLANTED: moved'
    assert moved_sources(_LOCK, tip=lambda _url: '') == (), 'an unanswered tip is not a move'


def test_an_upgrade_is_due_daily_or_when_a_pin_moved(tmp_path: Path) -> None:
    root, head = _planted(tmp_path, message=f'chore: up\n\n{TRAILER}: 2026-10-15')
    pinned = {'tip': lambda _url: _PIN}
    assert due(root, head, 'update/lock', today=date(2026, 10, 15), **pinned) == ''
    assert 'last upgraded 2026-10-15' in due(root, head, 'update/lock', today=date(2026, 10, 16), **pinned)
    assert 'moved past the lock' in due(root, head, 'update/lock', today=date(2026, 10, 15), tip=lambda _url: 'b' * 40)
    never, at = _planted(tmp_path / 'n')
    assert due(never, at, 'update/lock', today=date(2026, 10, 15), **pinned) == 'uv.lock was never upgraded'


class _Forge:
    def __init__(self, titles: tuple[str, ...] = ()) -> None:
        self.titles, self.made = titles, []

    def issue_list(self, *, state: str, limit: int) -> tuple:
        del state, limit
        return tuple(type('I', (), {'title': t})() for t in self.titles)


def test_schedule_publishes_once_per_head_through_the_request_path(tmp_path: Path, monkeypatch) -> None:
    root, head = _planted(tmp_path)
    published = []
    monkeypatch.setattr(
        'lab_commons.dev.lockupgrade.publish',
        lambda _r, _c, producer, sha, label: published.append((producer, sha, label)) or type('I', (), {'number': 3})(),
    )
    said = schedule(root, _Forge(), label='forge:request', today=date(2026, 10, 16), tip=lambda _url: _PIN)
    assert said.startswith(f'#3 request {PRODUCER} at {head[:12]}')
    assert published == [(PRODUCER, head, 'forge:request')]
    title = f'request {PRODUCER} at {head[:12]}'
    assert schedule(root, _Forge((title,)), label='l', today=date(2026, 10, 16), tip=lambda _url: _PIN) == ''
    assert len(published) == 1, 'PLANTED: an open request at this head is not opened twice'


def test_the_producer_commits_an_upgrade_on_the_update_branch_and_pushes_it(tmp_path: Path, monkeypatch) -> None:
    root, head = _planted(tmp_path)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '3')
    for i, (k, v) in enumerate((('user.name', 't'), ('user.email', 't@t'), ('core.hooksPath', '/dev/null'))):
        monkeypatch.setenv(f'GIT_CONFIG_KEY_{i}', k)
        monkeypatch.setenv(f'GIT_CONFIG_VALUE_{i}', v)
    assert main([], cwd=root, today=date(2026, 10, 16), relock=lambda _r: 0) == 0, 'unchanged: an empty upgrade'
    first = _git(root, 'rev-parse', 'refs/remotes/origin/update/lock')
    assert _git(root, 'rev-parse', f'{first}^') == head
    assert upgraded_on(root, first) is not None
    _git(root, 'checkout', '-q', '--detach', head)

    def moved(r: Path) -> int:
        (r / 'uv.lock').write_text(_LOCK.replace(_PIN, 'b' * 40), encoding='utf-8')
        return 0

    assert main([], cwd=root, today=date(2026, 10, 17), relock=moved) == 0
    second = _git(root, 'rev-parse', 'refs/remotes/origin/update/lock')
    assert _git(root, 'merge-base', '--is-ancestor', first, second) == '', 'the branch only grows'
    assert 'b' * 40 in _git(root, 'show', f'{second}:uv.lock')
    assert main([], cwd=root, today=date(2026, 10, 17), relock=lambda _r: 1) == 1, 'PLANTED: uv failing fails'


def test_a_serve_tick_says_what_it_opened_and_says_a_failure_rather_than_raising(tmp_path: Path, monkeypatch) -> None:
    root, _ = _planted(tmp_path)
    said: list[str] = []
    monkeypatch.setattr('lab_commons.dev.lockupgrade.schedule', lambda *_a, **_k: '#3 opened')
    serve_tick(root, _Forge(), today=date(2026, 10, 16), say=said.append)

    def broken(*_a: object, **_k: object) -> str:
        msg = 'origin unreachable'
        raise OSError(msg)

    monkeypatch.setattr('lab_commons.dev.lockupgrade.schedule', broken)
    serve_tick(root, _Forge(), today=date(2026, 10, 16), say=said.append)
    assert said == ['[serve] lock upgrade: #3 opened', '[serve] the lock upgrade was not scheduled: origin unreachable']
