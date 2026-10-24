"""``python -m lab_commons.hpc clean``: the one narrow door that deletes named directories under a root."""

from __future__ import annotations

from typing import Final

import pytest

from lab_commons.hpc import clean
from lab_commons.hpc.grants import Grant, Machine

ROOT: Final = '/home/u/tree/output'


@pytest.mark.parametrize('name', ['e69-shard72', 'a', 'x.y_z-1', '9'])
def test_a_plain_component_is_a_name(name: str) -> None:
    assert clean.valid_name(name)


@pytest.mark.parametrize('name', ['', '.', '..', 'a/b', '*', 'a b', '/abs', '-rf', '.hidden', 'a\\b', 'a?'])
def test_anything_else_is_refused(name: str) -> None:
    assert not clean.valid_name(name)


def test_containment_is_the_resolved_parent_equal_to_the_resolved_root() -> None:
    assert clean.contained(ROOT, f'{ROOT}/e69')
    assert not clean.contained(ROOT, f'{ROOT}/e69/deeper')
    assert not clean.contained(ROOT, '/elsewhere/e69')
    assert not clean.contained(ROOT, ROOT)


def test_a_running_workdir_under_a_target_is_named() -> None:
    squeue = f'{ROOT}/e69/run\n/home/u/other\n{ROOT}/e690\n\n'
    assert clean.running_under(squeue, [f'{ROOT}/e69', f'{ROOT}/e70']) == {f'{ROOT}/e69': [f'{ROOT}/e69/run']}
    assert clean.running_under(f'{ROOT}/e70\n', [f'{ROOT}/e70']) == {f'{ROOT}/e70': [f'{ROOT}/e70']}


class FakeCluster:
    """A runner over a fake remote tree: realpath is identity, ``rm`` removes all but foreign-owned paths."""

    def __init__(self, tree: dict[str, str], squeue: str = '', *, root_exists: bool = True) -> None:
        """*tree* maps each remote path to its owner (``me`` is this account)."""
        self.tree = dict(tree)  # path -> owner
        self.squeue = squeue
        self.root_exists = root_exists
        self.commands: list[str] = []

    def __call__(self, command: str, stdin: str | None = None) -> str:
        self.commands.append(command)
        kind, _, arg = command.partition(clean.MARK)
        path = arg.strip().strip("'")
        if command.startswith('squeue'):
            return self.squeue
        if clean.PROBE in kind:
            exists = path in self.tree
            root = ROOT if self.root_exists else ''
            return f'{root}\t{path}\t{int(exists)}\t{"1.0G" if exists else ""}\n'
        if clean.DELETE in kind:
            self.tree = {p: o for p, o in self.tree.items() if not (p.startswith(path) and o == 'me')}
            left = {p: o for p, o in self.tree.items() if p == path or p.startswith(path + '/')}
            if not left:
                return 'gone\n'
            foreign = [f'{o}\t{p}' for p, o in sorted(left.items()) if o != 'me']
            return '\n'.join(foreign) + '\n'
        raise AssertionError(command)


def _machine() -> Machine:
    return Machine(session=None, grants=(Grant(user='u', hosts=('login',), slurm_account='acct'),))


def _clean(argv: list[str], cluster: FakeCluster) -> int:
    return clean.main(argv, machine=_machine(), reach=lambda _grant: cluster)


def test_a_named_directory_is_deleted_and_verified_gone(capsys: pytest.CaptureFixture[str]) -> None:
    cluster = FakeCluster({f'{ROOT}/e69': 'me', f'{ROOT}/e69/f': 'me'})
    assert _clean(['--root', ROOT, 'e69'], cluster) == 0
    assert not cluster.tree
    assert f'gone: {ROOT}/e69' in capsys.readouterr().out


def test_dry_run_deletes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    cluster = FakeCluster({f'{ROOT}/e69': 'me'})
    assert _clean(['--root', ROOT, '--dry-run', 'e69'], cluster) == 0
    assert cluster.tree
    assert not any(clean.DELETE in c for c in cluster.commands)
    assert f'would delete {ROOT}/e69 (1.0G)' in capsys.readouterr().out


def test_an_absent_name_is_reported_and_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert _clean(['--root', ROOT, 'nope'], FakeCluster({})) == 0
    assert f'absent: {ROOT}/nope' in capsys.readouterr().out


def test_a_running_job_refuses_the_whole_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    cluster = FakeCluster({f'{ROOT}/a': 'me', f'{ROOT}/b': 'me'}, squeue=f'{ROOT}/b/run\n')
    assert _clean(['--root', ROOT, 'a', 'b'], cluster) == 2
    assert len(cluster.tree) == 2
    assert f'{ROOT}/b/run' in capsys.readouterr().err


def test_a_foreign_owned_residual_is_reported_with_its_owner_and_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    record = f'{ROOT}/e69/.lab-hpc/rec'
    cluster = FakeCluster({f'{ROOT}/e69': 'me', f'{ROOT}/e69/.lab-hpc': 'me', record: 'other'})
    assert _clean(['--root', ROOT, 'e69'], cluster) == 1
    err = capsys.readouterr().err
    assert f'NOT REMOVED: {ROOT}/e69' in err
    assert f'other\t{record}' in err


@pytest.mark.parametrize(
    'argv',
    [
        ['e69'],
        ['--root', 'relative/out', 'e69'],
        ['--root', '/a/../b', 'e69'],
        ['--root', ROOT],
        ['--root', ROOT, '../etc'],
        ['--root', ROOT, 'ok', 'a/b'],
    ],
)
def test_usage_and_validation_exit_two_before_reaching_the_cluster(argv: list[str]) -> None:
    cluster = FakeCluster({f'{ROOT}/ok': 'me'})
    assert _clean(argv, cluster) == 2
    assert not cluster.commands


def test_a_missing_root_is_refused() -> None:
    cluster = FakeCluster({f'{ROOT}/e69': 'me'}, root_exists=False)
    assert _clean(['--root', ROOT, 'e69'], cluster) == 2
    assert cluster.tree


def test_a_target_resolving_outside_the_root_is_refused() -> None:
    class Escaping(FakeCluster):
        def __call__(self, command: str, stdin: str | None = None) -> str:
            out = super().__call__(command, stdin)
            return out.replace(f'\t{ROOT}/link\t', '\t/etc\t') if clean.PROBE in command else out

    cluster = Escaping({f'{ROOT}/link': 'me'})
    assert _clean(['--root', ROOT, 'link'], cluster) == 2
    assert cluster.tree


def test_several_grants_need_one_named() -> None:
    two = Machine(
        session=None,
        grants=(
            Grant(user='u', hosts=('login',), slurm_account='a'),
            Grant(user='v', hosts=('login',), slurm_account='b'),
        ),
    )
    cluster = FakeCluster({f'{ROOT}/e69': 'me'})
    assert clean.main(['--root', ROOT, 'e69'], machine=two, reach=lambda _g: cluster) == 2
    assert clean.main(['--root', ROOT, '--grant', 'v', 'e69'], machine=two, reach=lambda _g: cluster) == 0
