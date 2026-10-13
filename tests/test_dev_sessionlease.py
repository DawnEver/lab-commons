"""ONE-WRITER-PER-SESSION-BRANCH, the lease half: taken, renewed, expired and taken over ON ORIGIN.

Every case runs against a REAL bare origin. The names are declared non-defaults (``int/line``,
``stable``, ``feat/s``) so a reader that guessed ``integrate/main`` reads green on nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lab_commons.dev import sessionlease
from lab_commons.dev.sessionlease import LeaseRefused, identity, read, release, take, write

_GIT = shutil.which('git') or 'git'
_DECL = (
    "[tool.lab_commons.branchset]\ntrunk = 'stable'\nsessions = ['int/line', 'feat/s']\nlease_hours = 2\n\n"
    "[tool.lab_commons.integrator]\nintegration = 'int/line'\ntrunk = 'stable'\n"
)
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [_GIT, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone of a bare origin carrying ``stable``, ``int/line`` and ``feat/s``; no lease yet."""
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    monkeypatch.delenv('LAB_COMMONS_SESSION', raising=False)
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '--bare', '-b', 'stable', str(origin))
    work = tmp_path / 'work'
    _git(tmp_path, 'clone', str(origin), str(work))
    (work / 'pyproject.toml').write_text(_DECL, encoding='utf-8')
    _git(work, 'checkout', '-b', 'stable')
    _git(work, 'add', 'pyproject.toml')
    _git(work, 'commit', '-m', 'chore: seed')
    _git(work, 'branch', 'int/line')
    _git(work, 'branch', 'feat/s')
    _git(work, 'push', 'origin', 'stable', 'int/line', 'feat/s')
    return work


def test_the_session_is_named_by_the_environment_never_by_a_host_or_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('LAB_COMMONS_SESSION', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    assert identity() is None
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'agent-1')
    assert identity() == 'agent-1'
    monkeypatch.setenv('LAB_COMMONS_SESSION', 'alice')
    assert identity() == 'alice', 'a declared session name outranks the agent id'


def test_a_lease_taken_lives_on_origin_with_its_holder_and_expiry(repo: Path) -> None:
    taken = take(repo, 'feat/s', who='alice', where='G', now=NOW)
    assert _git(repo, 'ls-remote', 'origin', 'refs/leases/feat/s').split()[0] == taken.sha
    found = read(repo, 'feat/s')
    assert found is not None
    assert (found.holder, found.where, found.expires) == ('alice', 'G', NOW + timedelta(hours=2))
    assert found.live(NOW)
    assert not found.live(NOW + timedelta(hours=2))


def test_a_live_lease_refuses_another_session_and_names_the_holder(repo: Path) -> None:
    take(repo, 'feat/s', who='alice', where='G', now=NOW)
    with pytest.raises(LeaseRefused, match=r'alice.*2026-10-13T14:00:00Z'):
        take(repo, 'feat/s', who='bob', where='H', now=NOW + timedelta(hours=1))
    found = read(repo, 'feat/s')
    assert found is not None
    assert found.holder == 'alice'


def test_an_expired_lease_is_taken_over_and_the_lease_history_only_grows(repo: Path) -> None:
    first = take(repo, 'feat/s', who='alice', where='G', now=NOW)
    over = take(repo, 'feat/s', who='bob', where='H', now=NOW + timedelta(hours=3))
    assert over.holder == 'bob'
    assert _git(repo, 'rev-parse', f'{over.sha}^') == first.sha


def test_the_holder_renews_and_releases(repo: Path) -> None:
    take(repo, 'feat/s', who='alice', where='G', now=NOW)
    renewed = take(repo, 'feat/s', who='alice', where='K', now=NOW + timedelta(hours=1))
    assert renewed.expires == NOW + timedelta(hours=3)
    assert renewed.where == 'K', 'the session moved host and its lease follows it'
    release(repo, 'feat/s', who='alice', now=NOW + timedelta(hours=1))
    assert take(repo, 'feat/s', who='bob', where='H', now=NOW + timedelta(hours=1)).holder == 'bob'


def test_release_by_a_non_holder_is_refused(repo: Path) -> None:
    take(repo, 'feat/s', who='alice', where='G', now=NOW)
    with pytest.raises(LeaseRefused, match='alice'):
        release(repo, 'feat/s', who='bob', now=NOW)


def test_the_write_is_compare_and_swap_a_stale_parent_loses(repo: Path) -> None:
    """Two takers that read the same old lease: the second write is a non-fast-forward and is refused."""
    old = take(repo, 'feat/s', who='alice', where='G', now=NOW)
    take(repo, 'feat/s', who='alice', where='G', now=NOW + timedelta(minutes=1))
    with pytest.raises(LeaseRefused, match='moved'):
        write(repo, 'feat/s', who='bob', where='H', expires=NOW + timedelta(hours=9), parent=old.sha)
    found = read(repo, 'feat/s')
    assert found is not None
    assert found.holder == 'alice'


def test_an_undeclared_branch_has_no_lease(repo: Path) -> None:
    with pytest.raises(LeaseRefused, match='not a declared session branch'):
        take(repo, 'lane/x', who='alice', where='G', now=NOW)


def test_the_cli_takes_with_the_environments_identity_and_refuses_without_one(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sessionlease.main(['take', 'feat/s', '--root', str(repo)]) == 1
    assert 'LAB_COMMONS_SESSION' in capsys.readouterr().err
    monkeypatch.setenv('LAB_COMMONS_SESSION', 'carol')
    assert sessionlease.main(['take', 'feat/s', '--root', str(repo)]) == 0
    assert sessionlease.main(['show', '--root', str(repo)]) == 0
    out = capsys.readouterr().out
    assert 'feat/s' in out
    assert 'carol' in out
    assert 'int/line' in out
