"""GITHUB-PRS-ARRIVE-ON-THE-FORGE: a GitHub mirror PR is carried to gitea as ``pr/gh-<N>``, once, and closed after.

Real repositories -- a bare gitea ``origin``, its clone, and a bare GitHub mirror holding ``refs/pull/<N>/head``
-- and two stateful fake forges (gitea and GitHub). The evidence is the refs the bare origin holds and the
pulls and comments each forge holds.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from _branch_repos import advance, clone_pair, commit
from _forge_fake import git_out, run_git
from _forge_state import StateForge

from lab_commons.dev.branchset import branchset_from
from lab_commons.dev.forge import Forge
from lab_commons.dev.forgemirror import PREFIX, drop_registrations, mirror, registers_only
from lab_commons.dev.forgepulls import Marker, marker
from lab_commons.dev.lanepolicy import Policy
from lab_commons.hpc.declared import pyproject_at

_DECL = "[tool.lab_commons.branchset]\ntrunk = 'main'\nsessions = ['integrate/main']\n"
_HACK = _DECL.replace("['integrate/main']", "['integrate/main', 'claude/x']")


def _planted(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """``(origin bare, clone, github bare, head)``: GitHub PR 1 is A, the registration hack, then B."""
    origin, work = clone_pair(tmp_path)
    advance(work, 'integrate/main', 'pyproject.toml', _DECL)
    run_git(work, 'checkout', '-q', '-B', 'claude/x', 'origin/integrate/main')
    commit(work, 'a.txt', 'a\n', 'feat: a')
    commit(work, 'pyproject.toml', _HACK, 'chore: register claude/x as a session')
    head = commit(work, 'b.txt', 'b\n', 'feat: b')
    github = tmp_path / 'github.git'
    run_git(tmp_path, 'init', '-q', '--bare', str(github))
    run_git(work, 'push', '-q', '--no-verify', str(github), f'{head}:refs/pull/1/head')
    run_git(work, 'checkout', '-q', 'main')
    return origin, work, github, head


def _forges() -> tuple[StateForge, StateForge, int]:
    gitea, hub = StateForge(), StateForge(Forge('github.com', 'o', 'r'))
    number = hub.add_pull('claude/x', 'main', title='feat: a and b', body='why')
    return gitea, hub, number


def test_a_commit_that_only_registers_its_branch_is_recognised_and_one_that_does_more_is_not(tmp_path: Path) -> None:
    """PLANTED control: the same registration plus one more manifest key is real content, and is kept."""
    _, work, _, head = _planted(tmp_path)
    hack = git_out(work, 'rev-parse', f'{head}^')
    assert registers_only(work, hack, 'claude/x')
    assert not registers_only(work, hack, 'claude/other'), 'another branch registered is not its own'
    assert not registers_only(work, head, 'claude/x'), 'b.txt is content'
    run_git(work, 'checkout', '-q', hack)
    more = commit(work, 'pyproject.toml', _HACK + 'prune_merged = true\n', 'chore: register and opt in')
    assert not registers_only(work, more, 'claude/x')
    run_git(work, 'checkout', '-q', 'main')


def test_dropping_the_hack_keeps_everything_else_and_gives_one_sha(tmp_path: Path) -> None:
    _, work, _, head = _planted(tmp_path)
    base = 'refs/remotes/origin/integrate/main'
    dropped = drop_registrations(work, base, head, 'claude/x')
    assert dropped != head
    assert drop_registrations(work, base, head, 'claude/x') == dropped, 'the replay is deterministic'
    assert git_out(work, 'log', '--format=%s', f'{base}..{dropped}').splitlines() == ['feat: b', 'feat: a']
    assert branchset_from(pyproject_at(work, dropped), 'p').sessions == frozenset({'integrate/main'})
    assert git_out(work, 'show', f'{dropped}:b.txt') == 'b'
    assert git_out(work, 'log', '-1', '--format=%an %aI', dropped) == git_out(
        work, 'log', '-1', '--format=%an %aI', head
    )
    first = git_out(work, 'rev-parse', f'{head}^^')
    assert drop_registrations(work, base, first, 'claude/x') == first, 'nothing to drop: the head itself'


def test_a_github_pr_is_carried_once_linked_once_and_closed_when_its_forge_pr_merges(tmp_path: Path) -> None:
    origin, work, github, _ = _planted(tmp_path)
    gitea, hub, number = _forges()
    policy = dataclasses.replace(Policy(), mirror=str(github))
    (line,) = mirror(work, gitea, hub, policy)
    branch = f'{PREFIX}{number}'
    carried = git_out(origin, 'rev-parse', f'refs/heads/{branch}')
    assert 'pushed' in line
    assert 'opened' in line
    assert branchset_from(pyproject_at(origin, carried), 'p').sessions == frozenset({'integrate/main'})
    ((forge_number, pull),) = gitea.pulls.items()
    assert (pull['head']['ref'], pull['base']['ref'], pull['title']) == (branch, 'integrate/main', 'feat: a and b')
    assert hub.pulls[number]['html_url'] in pull['body']
    (link,) = hub.comments[number]
    assert marker(Marker.MIRROR) in link['body']
    assert pull['html_url'] in link['body']

    (again,) = mirror(work, gitea, hub, policy)
    assert again.endswith('unchanged'), again
    assert len(gitea.pulls) == 1
    assert len(hub.comments[number]) == 1
    assert git_out(origin, 'rev-parse', f'refs/heads/{branch}') == carried
    assert hub.pulls[number]['state'] == 'open', 'PLANTED control: open while its forge PR is open'

    gitea.pulls[forge_number] |= {'state': 'closed', 'merged': True}
    (closed,) = mirror(work, gitea, hub, policy)
    assert 'merged; closed' in closed
    assert hub.pulls[number]['state'] == 'closed'
    (link,) = hub.comments[number]
    assert 'was merged' in link['body']
    assert len(gitea.pulls) == 1, 'a finished PR is never mirrored again'
