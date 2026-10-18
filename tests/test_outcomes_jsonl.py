"""A0.6: the JSONL reader tolerates a torn LAST line and refuses a torn middle line."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.outcomes import TornRecord, read_jsonl


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    read = read_jsonl(tmp_path / 'absent.jsonl')
    assert (read.rows, read.good_bytes, read.torn) == ((), 0, False)


def test_complete_lines_are_read_in_order(tmp_path: Path) -> None:
    path = tmp_path / 'r.jsonl'
    path.write_bytes(b'{"a":1}\n{"a":2}\n')
    read = read_jsonl(path)
    assert read.rows == ({'a': 1}, {'a': 2})
    assert read.good_bytes == path.stat().st_size
    assert not read.torn


def test_planted_control_a_torn_last_line_is_tolerated(tmp_path: Path) -> None:
    """A writer killed mid-append leaves an unterminated tail; everything before it stands."""
    path = tmp_path / 'r.jsonl'
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":')
    read = read_jsonl(path)
    assert read.rows == ({'a': 1}, {'a': 2})
    assert read.good_bytes == len(b'{"a":1}\n{"a":2}\n')
    assert read.torn


def test_an_unterminated_tail_is_torn_even_when_it_parses(tmp_path: Path) -> None:
    """The writer writes a line and its newline together: no newline means the append did not finish."""
    path = tmp_path / 'r.jsonl'
    path.write_bytes(b'{"a":1}\n{"a":2}')
    read = read_jsonl(path)
    assert read.rows == ({'a': 1},)
    assert read.torn


def test_planted_control_a_torn_middle_line_is_refused(tmp_path: Path) -> None:
    """A damaged line with lines after it is not a crash signature; reading past it would lie."""
    path = tmp_path / 'r.jsonl'
    path.write_bytes(b'{"a":1}\n{"a":\n{"a":3}\n')
    with pytest.raises(TornRecord, match='line 2'):
        read_jsonl(path)


def test_a_line_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / 'r.jsonl'
    path.write_bytes(b'[1, 2]\n')
    with pytest.raises(TornRecord, match='line 1'):
        read_jsonl(path)
