"""``lab_commons.paths`` — app-agnostic path resolution + per-run output directories.

Ported from consumer-a's ``tests/unit/core/test_mylab_logging.py`` (the ``TestPathScheme``
/ ``TestResolveHome`` classes), renamed to this package's import path. Proves (a) the path
primitives produce the nested ``logs/yy/mm/dd/[name/]HH-MM-SS/`` scheme, and (b) ``resolve_home``
honors its env var and falls back correctly.
"""

import subprocess
import sys

from lab_commons import paths


class TestPathScheme:
    def test_run_output_dir_nested_named_and_run_stamped(self, mocker, tmp_path) -> None:
        """<root>/logs/<yy>/<mm>/<dd>/<name>/<HH-MM-SS>/ — memoized stamp reused."""
        mocker.patch.object(paths, '_run_stamp', None)
        mocker.patch.object(paths, '_run_date', None)
        mocker.patch.object(paths, '_now_stamp', return_value='08-09-10')
        mocker.patch.object(paths, '_now_date', return_value=('26', '07', '20'))

        d = paths.run_output_dir('myapp', 'case_x', root=tmp_path)
        assert d == tmp_path / 'logs' / '26' / '07' / '20' / 'case_x' / '08-09-10'
        assert d.is_dir()
        # Second call is memoized onto the same run folder.
        assert paths.run_output_dir('myapp', 'case_x', root=tmp_path) == d

    def test_run_output_dir_sanitizes_name(self, tmp_path) -> None:
        d = paths.run_output_dir('myapp', 'a/b\\c', root=tmp_path)
        assert (tmp_path / 'logs') in d.parents
        # the name segment is flattened (its parent within the date tree)
        assert d.parent.name == 'a_b_c'

    def test_run_stamp_and_run_date_memoized(self, mocker) -> None:
        mocker.patch.object(paths, '_run_stamp', None)
        mocker.patch.object(paths, '_run_date', None)
        stamp = mocker.patch.object(paths, '_now_stamp', return_value='01-02-03')
        date = mocker.patch.object(paths, '_now_date', return_value=('26', '07', '20'))
        assert paths.run_stamp() == '01-02-03'
        assert paths.run_stamp() == '01-02-03'
        assert paths.run_date() == ('26', '07', '20')
        assert paths.run_date() == ('26', '07', '20')
        stamp.assert_called_once()
        date.assert_called_once()

    def test_unique_run_dir_collision_safe(self, mocker, tmp_path) -> None:
        mocker.patch.object(paths, '_run_stamp', None)
        mocker.patch.object(paths, '_now_stamp', return_value='12-34-56')
        a = paths.unique_run_dir(tmp_path, 'model')
        b = paths.unique_run_dir(tmp_path, 'model')
        assert a.name == 'model-12-34-56'
        assert b.name == 'model-12-34-56-2'
        assert a.is_dir()
        assert b.is_dir()


class TestResolveHome:
    def test_env_var_wins(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv('MYAPP_HOME', str(tmp_path))
        assert paths.resolve_home('myapp') == tmp_path.resolve()

    def test_explicit_env_var_name(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv('MYAPP_HOME', raising=False)
        monkeypatch.setenv('LEGACY_HOME', str(tmp_path))
        assert paths.resolve_home('myapp', env_var='LEGACY_HOME') == tmp_path.resolve()

    def test_output_and_config_roots_split(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv('MYAPP_HOME', str(tmp_path))
        assert paths.output_root('myapp') == tmp_path.resolve() / 'output'
        assert paths.config_root('myapp') == tmp_path.resolve() / 'config'


# A child run: wait at a file barrier, claim a run dir under a frozen stamp, drop a marker file
# in it, and print what it claimed and what it saw. ``planted`` restores the old exist_ok=True
# REUSE inside the child, the control that proves the two-process test can fail.
_CHILD = r"""
import os, pathlib, sys, time
import subprocess
import sys

from lab_commons import paths
root, go, me, planted = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4] == '1'
if planted:
    real = pathlib.Path.mkdir
    pathlib.Path.mkdir = lambda self, mode=0o777, parents=False, exist_ok=False: real(self, mode, parents, True)
paths._now_stamp = lambda: '16-22-39'
paths._now_date = lambda: ('26', '10', '15')
deadline = time.monotonic() + 60
while not go.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
d = paths.run_output_dir('myapp', 'case', root=root)
(d / f'{me}.txt').write_text(me)
time.sleep(0.5)
print(d)
print(sorted(p.name for p in d.iterdir()))
"""


def _two_runs_in_one_second(tmp_path, *, planted: bool) -> list[tuple[str, str]]:

    go = tmp_path / 'go'
    procs = [
        subprocess.Popen(
            [sys.executable, '-c', _CHILD, str(tmp_path), str(go), me, '1' if planted else '0'],
            stdout=subprocess.PIPE,
            text=True,
            encoding='utf-8',
        )
        for me in ('a', 'b')
    ]
    go.write_text('')
    outs = [p.communicate(timeout=120)[0].splitlines() for p in procs]
    assert all(p.returncode == 0 for p in procs)
    return [(o[0], o[1]) for o in outs]


class TestRunOutputDirClaim:
    """Two runs started in the same second never share one run dir (the claim, not the stamp)."""

    def _freeze(self, mocker) -> None:
        mocker.patch.object(paths, '_run_stamp', None)
        mocker.patch.object(paths, '_run_date', None)
        mocker.patch.object(paths, '_claimed', {})
        mocker.patch.object(paths, '_now_stamp', return_value='16-22-39')
        mocker.patch.object(paths, '_now_date', return_value=('26', '10', '15'))

    def test_a_taken_stamp_advances_to_the_next_suffix_and_stays(self, mocker, tmp_path) -> None:
        self._freeze(mocker)
        parent = tmp_path / 'logs' / '26' / '10' / '15' / 'case'
        (parent / '16-22-39').mkdir(parents=True)
        d = paths.run_output_dir('myapp', 'case', root=tmp_path)
        assert d == parent / '16-22-39-2'
        assert paths.run_output_dir('myapp', 'case', root=tmp_path) == d

    def test_an_unclaimed_stamp_is_the_plain_stamp_and_stays(self, mocker, tmp_path) -> None:
        self._freeze(mocker)
        d = paths.run_output_dir('myapp', 'case', root=tmp_path)
        assert d.name == '16-22-39'
        assert paths.run_output_dir('myapp', 'case', root=tmp_path) == d

    def test_a_different_name_claims_under_its_own_parent(self, mocker, tmp_path) -> None:
        self._freeze(mocker)
        a = paths.run_output_dir('myapp', 'one', root=tmp_path)
        b = paths.run_output_dir('myapp', 'two', root=tmp_path)
        assert (a.parent.name, a.name) == ('one', '16-22-39')
        assert (b.parent.name, b.name) == ('two', '16-22-39')

    def test_two_processes_in_one_second_get_distinct_dirs(self, tmp_path) -> None:
        (da, seen_a), (db, seen_b) = _two_runs_in_one_second(tmp_path, planted=False)
        assert da != db
        assert seen_a == "['a.txt']"
        assert seen_b == "['b.txt']"

    def test_planted_control_exist_ok_reuse_shares_the_dir(self, tmp_path) -> None:
        """The control: the old REUSE restored, the same two processes land in ONE dir."""
        (da, _), (db, _) = _two_runs_in_one_second(tmp_path, planted=True)
        assert da == db
