"""``lab_commons.paths`` — app-agnostic path resolution + per-run output directories.

Ported from motronics-studio's ``tests/unit/core/test_mylab_logging.py`` (the ``TestPathScheme``
/ ``TestResolveHome`` classes), renamed to this package's import path. Proves (a) the path
primitives produce the nested ``logs/yy/mm/dd/[name/]HH-MM-SS/`` scheme, and (b) ``resolve_home``
honors its env var and falls back correctly.
"""

from lab_commons import paths


class TestPathScheme:
    def test_run_output_dir_nested_named_and_run_stamped(self, mocker, tmp_path):
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

    def test_run_output_dir_sanitizes_name(self, tmp_path):
        d = paths.run_output_dir('myapp', 'a/b\\c', root=tmp_path)
        assert (tmp_path / 'logs') in d.parents
        # the name segment is flattened (its parent within the date tree)
        assert d.parent.name == 'a_b_c'

    def test_run_stamp_and_run_date_memoized(self, mocker):
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

    def test_unique_run_dir_collision_safe(self, mocker, tmp_path):
        mocker.patch.object(paths, '_run_stamp', None)
        mocker.patch.object(paths, '_now_stamp', return_value='12-34-56')
        a = paths.unique_run_dir(tmp_path, 'model')
        b = paths.unique_run_dir(tmp_path, 'model')
        assert a.name == 'model-12-34-56'
        assert b.name == 'model-12-34-56-2'
        assert a.is_dir() and b.is_dir()


class TestResolveHome:
    def test_env_var_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv('MYAPP_HOME', str(tmp_path))
        assert paths.resolve_home('myapp') == tmp_path.resolve()

    def test_explicit_env_var_name(self, monkeypatch, tmp_path):
        monkeypatch.delenv('MYAPP_HOME', raising=False)
        monkeypatch.setenv('LEGACY_HOME', str(tmp_path))
        assert paths.resolve_home('myapp', env_var='LEGACY_HOME') == tmp_path.resolve()

    def test_output_and_config_roots_split(self, monkeypatch, tmp_path):
        monkeypatch.setenv('MYAPP_HOME', str(tmp_path))
        assert paths.output_root('myapp') == tmp_path.resolve() / 'output'
        assert paths.config_root('myapp') == tmp_path.resolve() / 'config'
