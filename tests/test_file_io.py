"""``lab_commons.file_io`` — TOML read/write + filesystem-presence helpers.

Ported from motronics-studio's ``tests/unit/core/utils/test_file_io.py``, renamed to this
package's import path. Rewritten against real filesystem behavior (``tmp_path``) rather than
mocked ``Path`` methods, matching the split-out style of ``test_log.py`` / ``test_paths.py``.
"""

import pytest

from lab_commons.file_io import check_path, list_files_in_dir, read_toml, save_toml


class TestTomlRoundTrip:
    def test_save_then_read_path(self, tmp_path):
        file_path = tmp_path / 'sub' / 'data.toml'
        save_toml(file_path, {'key': 'value'})
        assert read_toml(file_path) == {'key': 'value'}

    def test_read_accepts_str_path(self, tmp_path):
        file_path = tmp_path / 'data.toml'
        save_toml(file_path, {'a': 1})
        assert read_toml(str(file_path)) == {'a': 1}

    def test_read_accepts_in_memory_bytes(self):
        # A str is always treated as a path, never content -- bytes is the in-memory route.
        assert read_toml(b'key = "value"\n') == {'key': 'value'}

    def test_read_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError, match='Failed to open file'):
            read_toml(tmp_path / 'missing.toml')

    def test_save_creates_missing_parent_dirs(self, tmp_path):
        file_path = tmp_path / 'a' / 'b' / 'c.toml'
        save_toml(file_path, {'x': 'y'})
        assert file_path.is_file()


class TestCheckPath:
    def test_existing_path_returns_true(self, tmp_path):
        existing = tmp_path / 'present.txt'
        existing.write_text('hi')
        assert check_path(existing) is True

    def test_missing_dir_is_created_and_returns_false(self, tmp_path):
        target = tmp_path / 'new_dir'
        assert check_path(target, is_dir=True) is False
        assert target.is_dir()

    def test_missing_file_copies_default(self, tmp_path):
        default = tmp_path / 'default.txt'
        default.write_text('fallback')
        target = tmp_path / 'nested' / 'target.txt'
        assert check_path(target, default_path=default) is False
        assert target.read_text() == 'fallback'

    def test_missing_file_without_default_only_creates_parent(self, tmp_path):
        target = tmp_path / 'nested' / 'target.txt'
        assert check_path(target) is False
        assert target.parent.is_dir()
        assert not target.exists()


class TestListFilesInDir:
    def test_filters_by_suffix(self, tmp_path):
        (tmp_path / 'a.toml').write_text('')
        (tmp_path / 'b.txt').write_text('')
        (tmp_path / 'c.toml').write_text('')
        result = list_files_in_dir(tmp_path, file_name_suffix='.toml')
        assert sorted(result) == ['a.toml', 'c.toml']

    def test_no_suffix_returns_all_files(self, tmp_path):
        (tmp_path / 'a.toml').write_text('')
        (tmp_path / 'b.txt').write_text('')
        result = list_files_in_dir(tmp_path)
        assert sorted(result) == ['a.toml', 'b.txt']

    def test_missing_dir_returns_empty_list(self, tmp_path):
        missing = tmp_path / 'does_not_exist'
        assert list_files_in_dir(missing) == []
        # check_path's is_dir=True side effect still creates it (documented behavior).
        assert missing.is_dir()


if __name__ == '__main__':
    pytest.main([__file__])
