"""Read an append-only JSONL file whose writer may have been killed mid-append.

Every line the writer completes ends in a newline, written together with the line. So an
UNTERMINATED tail is the one signature a kill leaves: it is dropped and reported (``torn``), and
:attr:`JsonlRead.good_bytes` says where the next append must start. A terminated line that does not
parse has lines after it or was committed damaged -- that is not a crash signature, and reading past
it would present a record with a hole as whole, so it is refused with :class:`TornRecord`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ['JsonlRead', 'TornRecord', 'read_jsonl']


class TornRecord(ValueError):
    """A line other than the unterminated tail is not a JSON object."""


@dataclass(frozen=True)
class JsonlRead:
    """The complete rows, the byte length they occupy, and whether a torn tail was dropped."""

    rows: tuple[dict[str, Any], ...]
    good_bytes: int
    torn: bool


def read_jsonl(path: Path) -> JsonlRead:
    """Every complete row of *path*; a missing file is empty. See the module docstring."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return JsonlRead((), 0, torn=False)
    end = data.rfind(b'\n') + 1
    rows = []
    for number, line in enumerate(data[:end].split(b'\n')[:-1], start=1):
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict):
            msg = f'{path}: line {number} is not a JSON object and is not the last line: {line[:80]!r}'
            raise TornRecord(msg)
        rows.append(row)
    return JsonlRead(tuple(rows), end, torn=end < len(data))
