"""``lab_commons.file_io`` — TOML read/write + filesystem-presence helpers.

Ported from consumer-a's ``tests/unit/core/utils/test_file_io.py``, renamed to this
package's import path. Rewritten against real filesystem behavior (``tmp_path``) rather than
mocked ``Path`` methods, matching the split-out style of ``test_log.py`` / ``test_paths.py``.
"""

import datetime as dt
import tomllib

import pytest
import tomlkit

from lab_commons import file_io
from lab_commons.file_io import check_path, dumps, list_files_in_dir, read_toml, save_toml


class TestTomlRoundTrip:
    def test_save_then_read_path(self, tmp_path) -> None:
        file_path = tmp_path / 'sub' / 'data.toml'
        save_toml(file_path, {'key': 'value'})
        assert read_toml(file_path) == {'key': 'value'}

    def test_read_accepts_str_path(self, tmp_path) -> None:
        file_path = tmp_path / 'data.toml'
        save_toml(file_path, {'a': 1})
        assert read_toml(str(file_path)) == {'a': 1}

    def test_read_accepts_in_memory_bytes(self) -> None:
        # A str is always treated as a path, never content -- bytes is the in-memory route.
        assert read_toml(b'key = "value"\n') == {'key': 'value'}

    def test_read_missing_file_raises_oserror(self, tmp_path) -> None:
        with pytest.raises(OSError, match='Failed to open file'):
            read_toml(tmp_path / 'missing.toml')

    def test_save_creates_missing_parent_dirs(self, tmp_path) -> None:
        file_path = tmp_path / 'a' / 'b' / 'c.toml'
        save_toml(file_path, {'x': 'y'})
        assert file_path.is_file()


class TestCheckPath:
    def test_existing_path_returns_true(self, tmp_path) -> None:
        existing = tmp_path / 'present.txt'
        existing.write_text('hi')
        assert check_path(existing) is True

    def test_missing_dir_is_created_and_returns_false(self, tmp_path) -> None:
        target = tmp_path / 'new_dir'
        assert check_path(target, is_dir=True) is False
        assert target.is_dir()

    def test_missing_file_copies_default(self, tmp_path) -> None:
        default = tmp_path / 'default.txt'
        default.write_text('fallback')
        target = tmp_path / 'nested' / 'target.txt'
        assert check_path(target, default_path=default) is False
        assert target.read_text() == 'fallback'

    def test_missing_file_without_default_only_creates_parent(self, tmp_path) -> None:
        target = tmp_path / 'nested' / 'target.txt'
        assert check_path(target) is False
        assert target.parent.is_dir()
        assert not target.exists()


class TestListFilesInDir:
    def test_filters_by_suffix(self, tmp_path) -> None:
        (tmp_path / 'a.toml').write_text('')
        (tmp_path / 'b.txt').write_text('')
        (tmp_path / 'c.toml').write_text('')
        result = list_files_in_dir(tmp_path, file_name_suffix='.toml')
        assert sorted(result) == ['a.toml', 'c.toml']

    def test_no_suffix_returns_all_files(self, tmp_path) -> None:
        (tmp_path / 'a.toml').write_text('')
        (tmp_path / 'b.txt').write_text('')
        result = list_files_in_dir(tmp_path)
        assert sorted(result) == ['a.toml', 'b.txt']

    def test_missing_dir_returns_empty_list(self, tmp_path) -> None:
        missing = tmp_path / 'does_not_exist'
        assert list_files_in_dir(missing) == []
        # check_path's is_dir=True side effect still creates it (documented behavior).
        assert missing.is_dir()


class TestOneTomlWriter:
    """Writes go through tomlkit, reads through stdlib tomllib -- the family's one TOML pair."""

    def test_the_writer_is_tomlkit_and_the_reader_is_tomllib(self) -> None:
        assert file_io.dumps is tomlkit.dumps
        assert file_io.loads is tomllib.loads

    def test_types_round_trip(self, tmp_path) -> None:
        data = {
            'f': 0.1,
            'small': 1e-12,
            'when': dt.datetime(2026, 11, 5, 12, 0, tzinfo=dt.UTC),
            'day': dt.date(2026, 11, 5),
            'nested': {'inner': {'xs': [1, 2, 3]}},
            'rows': [{'k': 1}, {'k': 2}],
        }
        path = tmp_path / 'r.toml'
        save_toml(path, data)
        assert read_toml(path) == data

    def test_the_written_text_is_pinned(self) -> None:
        # Consumer-observable: tables keep insertion order (rtoml hoisted arrays of tables above
        # tables), floats print as repr, sub-tables are block headers, never inline.
        text = dumps({'a': 1.0, 'b': 1e-12, 't': {'x': [1, 2]}, 'aot': [{'k': 1}, {'k': 2}]})
        assert text == 'a = 1.0\nb = 1e-12\n\n[t]\nx = [1, 2]\n\n[[aot]]\nk = 1\n\n[[aot]]\nk = 2\n'

    def test_none_is_refused_not_written_as_a_string(self, tmp_path) -> None:
        # rtoml wrote None as the STRING "null", which reads back as 'null' -- a silent change of type.
        with pytest.raises(TypeError):
            save_toml(tmp_path / 'n.toml', {'n': None})

    def test_a_refused_value_leaves_an_existing_file_untouched(self, tmp_path) -> None:
        # The value is serialised BEFORE the target is opened: a refusal must not truncate a good file.
        path = tmp_path / 'keep.toml'
        save_toml(path, {'a': 1})
        with pytest.raises(TypeError):
            save_toml(path, {'n': None})
        assert read_toml(path) == {'a': 1}

    def test_a_refused_value_leaves_no_file_on_a_fresh_path(self, tmp_path) -> None:
        path = tmp_path / 'fresh.toml'
        with pytest.raises(TypeError):
            save_toml(path, {'n': None})
        assert not path.exists()


if __name__ == '__main__':
    pytest.main([__file__])
