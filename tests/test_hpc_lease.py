"""Leased trees, run by REAL bash and git against a temporary ``$HOME``: acquire, release, and the reaper.

The scripts are what runs on the cluster, so they are executed, not string-matched: ``$HOME`` is a temp dir,
the "remote" is a local bare repository, and ``squeue`` is a stub printing what the test planted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from test_hpc_run import bash_executable

from lab_commons.hpc import lease
from lab_commons.hpc.lease import LeaseRefused

Runner = Callable[[str, str | None], str]
NOW = 1_800_000_000.0


def _git(*args: str, cwd: Path) -> str:
    git = shutil.which('git') or 'git'
    return subprocess.run([git, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def cluster(tmp_path: Path) -> tuple[Runner, Path, str, list[str]]:
    """A runner whose ``$HOME`` is a temp dir, a remote with two commits, and the queue the stub prints."""
    home, origin, work, stub = tmp_path / 'home', tmp_path / 'origin.git', tmp_path / 'work', tmp_path / 'bin'
    for d in (home, work, stub):
        d.mkdir()
    _git('init', '-q', '--bare', str(origin), cwd=tmp_path)
    _git('init', '-q', '-b', 'main', cwd=work)
    for n in (1, 2):
        (work / 'f.txt').write_text(str(n), encoding='utf-8')
        _git('add', 'f.txt', cwd=work)
        _git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', f'c{n}', cwd=work)
    _git('push', '-q', str(origin), 'main', cwd=work)
    queue: list[str] = []
    (stub / 'squeue').write_text('#!/bin/sh\ncat "$HOME/queue.txt" 2>/dev/null\n', encoding='utf-8', newline='\n')
    (stub / 'squeue').chmod(0o755)  # Linux runs only an executable stub; Git Bash runs any shebang file
    bash = bash_executable()

    def run(command: str, stdin: str | None = None) -> str:
        (home / 'queue.txt').write_text(''.join(q + '\n' for q in queue), encoding='utf-8', newline='\n')
        env = {**os.environ, 'HOME': home.as_posix()}
        script = f'export PATH="$(cd {stub.as_posix()!r} && pwd):$PATH"; {command}'  # no drive colon in PATH
        done = subprocess.run(
            [bash, '-c', script], input=stdin, capture_output=True, text=True, encoding='utf-8', env=env, check=True
        )
        return done.stdout

    return run, home, origin.as_posix(), queue


def _shas(origin: str) -> list[str]:
    return _git('--git-dir', origin, 'rev-list', 'main', cwd=Path(origin).parent).splitlines()


def test_a_lease_writes_its_record_and_checks_out_the_commit(cluster: tuple[Runner, Path, str, list[str]]) -> None:
    run, home, origin, _ = cluster
    sha = _shas(origin)[1]
    tree = lease.acquire(run, origin, sha, holder='lane-a', purpose='A/B of c1', ttl_hours=2, now=NOW)
    assert tree.endswith(f'/ci/leases/trees/{sha}')
    record = json.loads((home / 'ci/leases/records' / sha / 'lane-a.json').read_text(encoding='utf-8'))
    assert record == {
        'sha': sha,
        'repo_url': origin,
        'holder': 'lane-a',
        'purpose': 'A/B of c1',
        'created': int(NOW),
        'expires': int(NOW + 7200),
    }
    assert (home / 'ci/leases/trees' / sha / 'f.txt').read_text(encoding='utf-8') == '1'
    assert [x.holder for x in lease.held(run)] == ['lane-a']


@pytest.mark.parametrize(
    ('holder', 'purpose', 'ttl', 'why'),
    [
        ('../x', 'p', 1, 'plain token'),
        ('a b', 'p', 1, 'plain token'),
        ('lane', ' ', 1, 'purpose'),
        ('lane', 'p', 0, 'a lease ends'),
        ('lane', 'p', lease.MAX_TTL_HOURS + 1, 'a lease ends'),
    ],
)
def test_a_bad_lease_is_refused_before_anything_is_written(holder: str, purpose: str, ttl: float, why: str) -> None:
    calls: list[str] = []
    with pytest.raises(LeaseRefused, match=why):
        lease.acquire(
            lambda c, _s: calls.append(c) or '', 'u', 'a' * 40, holder=holder, purpose=purpose, ttl_hours=ttl, now=NOW
        )
    assert calls == [], 'refused before the cluster is touched'


def test_a_branch_name_is_never_leased() -> None:
    with pytest.raises(ValueError, match='40-hex'):
        lease.acquire(lambda _c, _s: '', 'u', 'main', holder='x', purpose='p', ttl_hours=1, now=NOW)


def test_the_reaper_keeps_live_and_busy_trees_and_removes_and_logs_the_rest(
    cluster: tuple[Runner, Path, str, list[str]],
) -> None:
    """A live lease, and a job under an expired one, each keep their tree; an expired free tree goes, logged.

    Planted control: a job in a SIBLING directory sharing the prefix (``<tree>0``) protects nothing.
    """
    run, home, origin, queue = cluster
    old, new = _shas(origin)[1], _shas(origin)[0]
    busy_tree = lease.acquire(run, origin, old, holder='busy', purpose='p', ttl_hours=1, now=NOW - 7200)
    live_tree = lease.acquire(run, origin, new, holder='live', purpose='p', ttl_hours=1, now=NOW)
    queue.append(f'{busy_tree}/.lab-hpc/run-1')
    assert [r.outcome for r in lease.reap(run, now=NOW)] == ['kept']
    assert Path(home / 'ci/leases/trees' / old).is_dir()
    queue[:] = [f'{busy_tree}0/elsewhere']
    done = lease.reap(run, now=NOW)
    assert [(r.sha, r.outcome, r.why) for r in done] == [(old, 'gone', 'every lease expired')]
    assert not (home / 'ci/leases/trees' / old).exists()
    assert not (home / 'ci/leases/records' / old).exists()
    assert Path(home / 'ci/leases/trees' / new / 'f.txt').is_file(), live_tree
    (log,) = (home / 'ci/leases/reaped').iterdir()
    assert log.name == '2027-01-15.jsonl'
    entry = json.loads(log.read_text(encoding='utf-8'))
    assert (entry['sha'], entry['why'], entry['when']) == (old, 'every lease expired', int(NOW))
    worktrees = _git('--git-dir', str(next((home / 'ci/cache').iterdir())), 'worktree', 'list', cwd=home)
    assert old[:7] not in worktrees, 'the cache forgot the reaped worktree'


def test_a_released_lease_frees_its_tree_but_another_holders_lease_keeps_it(
    cluster: tuple[Runner, Path, str, list[str]],
) -> None:
    run, home, origin, _ = cluster
    sha = _shas(origin)[0]
    for holder in ('a', 'b'):
        lease.acquire(run, origin, sha, holder=holder, purpose='p', ttl_hours=1, now=NOW)
    lease.release(run, sha, 'a')
    assert lease.reap(run, now=NOW) == []
    lease.release(run, sha, 'b')
    assert [r.why for r in lease.reap(run, now=NOW)] == ['no lease names it']
    assert not (home / 'ci/leases/trees' / sha).exists()


def test_a_commit_the_remote_lacks_is_refused(cluster: tuple[Runner, Path, str, list[str]]) -> None:
    run, home, origin, _ = cluster
    with pytest.raises(LeaseRefused, match='not on'):
        lease.acquire(run, origin, 'b' * 40, holder='x', purpose='p', ttl_hours=1, now=NOW)
    assert not (home / 'ci/leases/records').exists()


def test_a_job_dir_as_ada_reports_it_keeps_its_tree() -> None:
    """``squeue -o %Z`` as measured on Ada 2026-11-06: an array's tasks each name a run dir INSIDE the tree."""
    jobs = (Path(__file__).parent / '_hpc_fixtures' / 'ada-squeue-Z-2026-11-06.txt').read_text(encoding='utf-8')
    tree = jobs.splitlines()[0].split('/.lab-hpc/')[0]
    state = f'@@@ tree {tree}\n' + ''.join(f'@@@ job {line}\n' for line in jobs.splitlines())
    removed: list[str] = []

    def run(command: str, _stdin: str | None = None) -> str:
        if '/records/*/*.json' in command:
            return state
        removed.append(command)
        return ''

    assert [r.outcome for r in lease.reap(run, now=NOW)] == ['kept']
    assert removed == [], 'nothing was removed'
