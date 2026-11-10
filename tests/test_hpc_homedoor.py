"""The home door: reap at soft, refuse at hard by name with the top consumers; the tick's pass; the CLI reach."""

from __future__ import annotations

from typing import Final

import pytest

from lab_commons.hpc import homedoor
from lab_commons.hpc import run as run_module
from lab_commons.hpc.grants import Grant, Machine
from lab_commons.hpc.home import CENSUS_PREFIX, QUOTA, QUOTA_SCRIPT, STRAY_FILES, census_script
from lab_commons.hpc.homedoor import QuotaRefused
from lab_commons.hpc.shell import RunSpec
from lab_commons.hpc.tree import NotSubmitted

LIMIT: Final = 4_000_000
HEADER: Final = (
    'mmlsquota:user:HEADER:version:reserved:reserved:filesystemName:quotaType:id:name:blockUsage:blockQuota:'
    'blockLimit:blockInDoubt:blockGrace:filesUsage:filesQuota:filesLimit:filesInDoubt:filesGrace:remarks:fid:'
    'filesetname:'
)


def _quota(files: int) -> str:
    head = 'mmlsquota:user:0:1:::dss2fs01:USR:1:-:100:1048576000:1101004800:0:none'
    row = f'{head}:{files}:{LIMIT}:4040000:0:none::6:HOME:'
    return f'@@@ fs dss2fs01\n{HEADER}\n{row}\n@@@ quota-w\n'


class Home:
    """A cluster home answering the quota (one reading per call, the last repeating), census and lease state."""

    def __init__(self, *files: int, census: str = '', state: str = '') -> None:
        """*files*: each quota reading's file count, in order; *census* and *state*: what those scripts print."""
        self.files, self.census, self.state = list(files), census, state
        self.calls: list[str] = []

    def __call__(self, command: str, stdin: str | None = None) -> str:
        if command == QUOTA_SCRIPT:
            self.calls.append('quota')
            return _quota(self.files.pop(0) if len(self.files) > 1 else self.files[0]) if self.files else ''
        if command == census_script():
            self.calls.append('census')
            return self.census
        if '/records/*/*.json' in command:
            self.calls.append('reap')
            return self.state
        self.calls.append(command)
        return ''


def _at(fraction: float) -> int:
    return int(LIMIT * fraction)


def test_below_soft_the_guard_reads_once_and_reaps_nothing() -> None:
    home = Home(_at(QUOTA.soft - 0.01))
    said: list[str] = []
    assert homedoor.guard(home, 'tree acquire x', now=0, say=said.append) is not None
    assert home.calls == ['quota']
    assert said == []


def test_at_soft_the_guard_reaps_first_then_warns_and_proceeds() -> None:
    home = Home(_at(QUOTA.soft), _at(QUOTA.soft - 0.05))
    said: list[str] = []
    usage = homedoor.guard(home, 'tree acquire x', now=0, say=said.append)
    assert home.calls == ['quota', 'reap', 'quota'], 'reap BEFORE the warning, then re-read'
    assert usage is not None
    assert usage.files == _at(QUOTA.soft - 0.05)
    (warning,) = said
    assert warning.startswith('HOME-QUOTA-SOFT')
    assert 'tree release' in warning


def test_at_hard_the_guard_refuses_by_name_with_usage_and_top_consumers() -> None:
    census = f'{CENSUS_PREFIX}ab\t{STRAY_FILES + 1}\n{CENSUS_PREFIX}ci\t9000\n'
    home = Home(_at(QUOTA.hard), census=census)
    with pytest.raises(QuotaRefused) as refused:
        homedoor.guard(home, 'tree acquire x', now=0, say=lambda _t: None)
    text = str(refused.value)
    assert text.startswith('HOME-QUOTA-HARD: tree acquire x refused')
    assert f'files {_at(QUOTA.hard)}/{LIMIT}' in text
    assert f'~/ab: >= {STRAY_FILES + 1} files; ~/ci: 9000 files' in text
    assert 'clean --root' in text
    assert home.calls[:3] == ['quota', 'reap', 'quota'], 'past hard it still reaps first'


def test_a_reap_that_frees_enough_lets_the_act_through() -> None:
    """Planted control for reap-then-refuse: hard before the reap, below hard after it -- not refused."""
    home = Home(_at(QUOTA.hard + 0.02), _at(QUOTA.hard - 0.02))
    assert homedoor.guard(home, 'x', now=0, say=lambda _t: None) is not None


def test_an_unreadable_quota_is_warned_and_never_read_as_zero() -> None:
    said: list[str] = []
    assert homedoor.guard(Home(), 'x', now=0, say=said.append) is None
    assert 'unreadable' in said[0]


def test_the_home_pass_reaps_reads_and_names_strays_but_never_a_managed_root() -> None:
    census = ''.join(f'{CENSUS_PREFIX}{name}\t{STRAY_FILES + 1}\n' for name in ('ab', 'src', 'ci'))
    home = Home(_at(0.5), census=census)
    report = homedoor.home_pass(home, now=0, managed=('src',))
    assert home.calls == ['reap', 'quota', 'census'], 'every tick reaps expired leases, whatever the quota'
    assert [s.name for s in report.strays] == ['ab']
    assert report.level == 'ok'
    assert any(line.startswith('[home] STRAY-HOME-TREE: ~/ab') for line in report.lines())


@pytest.mark.parametrize(('fraction', 'level'), [(0.5, 'ok'), (QUOTA.soft, 'soft'), (QUOTA.hard, 'hard')])
def test_the_report_level_follows_the_declared_thresholds(fraction: float, level: str) -> None:
    assert homedoor.home_pass(Home(_at(fraction)), now=0).level == level


def test_tree_acquire_is_refused_at_hard_before_any_fetch() -> None:
    home = Home(_at(QUOTA.hard))
    rc = homedoor.tree_main(
        ['acquire', 'u', 'a' * 40, '--purpose', 'p', '--at', 'me@h'], connect=lambda *_: (home, 'h')
    )
    assert rc == 2
    assert not any('cat-file' in c for c in home.calls), 'nothing fetched'


def test_the_home_verb_exits_one_on_a_stray(capsys: pytest.CaptureFixture[str]) -> None:
    home = Home(_at(0.5), census=f'{CENSUS_PREFIX}ab\t{STRAY_FILES + 1}\n')
    assert homedoor.home_main(['--at', 'me@h'], connect=lambda *_: (home, 'h')) == 1
    assert 'STRAY-HOME-TREE' in capsys.readouterr().out
    assert homedoor.home_main(['--at', 'me@h'], connect=lambda *_: (Home(_at(0.5)), 'h')) == 0


def test_at_names_the_target_and_needs_no_machine_file() -> None:
    run, where = homedoor.reach('me@login.example', None)
    assert where == 'me@login.example'
    assert callable(run)


def test_run_submit_is_not_submitted_at_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run submit`` passes the same guard: past hard, nothing is built or queued, and it is INCONCLUSIVE."""
    home = Home(_at(QUOTA.hard))
    monkeypatch.setattr(run_module, 'probe', lambda *_a, **_k: None)
    monkeypatch.setattr(run_module, 'allocate', lambda *_a, **_k: (_grant(), None))
    built: list[str] = []
    monkeypatch.setattr(run_module, 'build_run', lambda *_a: built.append('built'))
    machine = Machine(session=None, grants=(_grant(),))
    with pytest.raises(NotSubmitted, match='HOME-QUOTA-HARD: run submit'):
        run_module.submit_run(RunSpec(sha='a' * 40, repo_url='u'), machine, lambda _g: home, cost=None, policy=None)
    assert built == []


def _grant() -> Grant:
    return Grant(user='me', hosts=('h',), slurm_account='a')


def test_a_leases_env_and_build_dirs_live_inside_it_on_the_shared_hardlinked_cache() -> None:
    """Per-tree uv caches and cargo targets were much of ~/ab: the hint keeps both INSIDE the reaped tree."""
    hint = homedoor.env_hint('/h/ci/leases/trees/abc')
    assert 'UV_CACHE_DIR="$HOME"/ci/uv-cache UV_LINK_MODE=hardlink uv venv /h/ci/leases/trees/abc/.venv' in hint
    assert '--target-dir /h/ci/leases/trees/abc/target' in hint
