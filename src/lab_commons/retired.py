"""RETIRED NAMES: a registry of DATA rows, refused by name with the replacement (v1 ruling D125 F4).

A name that is retired -- a verb, a term, a config key, a module path -- must not come back, and whoever meets it
must be told what it became. That is one mechanism for the whole family: a product's term and verb tables supply
ROWS ``(name, replacement, note)`` -- the note says why, or how to migrate -- and :class:`Registry` refuses a name
(:meth:`Registry.refuse`, raising :class:`RetiredName` with its door) and finds every retired spelling in a text
(:meth:`Registry.found_in`), so no product reimplements either. A FLAG spelling (leading ``-``) matches only as a
whole token, so ``--mode`` is not found in ``--model``; any other spelling matches as a substring, which is what
finds a retired module path inside a longer one. The kit's own registry, :mod:`lab_commons.hpc.retired`, is one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

__all__ = ['Registry', 'Retired', 'RetiredName']

_FIELDS = ('name', 'replacement', 'note')


class RetiredName(LookupError):
    """A retired name was used; the message names it, its replacement and the note."""


@dataclass(frozen=True)
class Retired:
    """One retired *name*: what replaced it, and why or how to migrate (*note*; may be empty)."""

    name: str
    replacement: str
    note: str = ''

    def door(self) -> str:
        """The refusal's sentence: the name, its replacement and the note."""
        note = f' ({self.note})' if self.note else ''
        return f'`{self.name}` is retired: use {self.replacement}{note}'


def _pattern(spelling: str) -> re.Pattern[str]:
    escaped = re.escape(spelling)
    return re.compile(rf'(?<![\w-]){escaped}(?![\w-])' if spelling.startswith('-') else escaped)


class Registry:
    """Retired names, each registered once, in the order given."""

    def __init__(self, rows: Iterable[Retired]) -> None:
        """Hold *rows*.

        Raises:
            ValueError: a name is registered twice.

        """
        self._rows: dict[str, Retired] = {}
        for row in rows:
            if row.name in self._rows:
                msg = f'retired name {row.name!r} is registered twice'
                raise ValueError(msg)
            self._rows[row.name] = row
        self._patterns = {name: _pattern(name) for name in self._rows}

    @classmethod
    def of(cls, rows: Iterable[Mapping[str, object]]) -> Registry:
        """A registry from data rows (a TOML array of tables): each with ``name``, ``replacement`` and ``note``.

        Raises:
            ValueError: a row lacks a field, or a name is registered twice.

        """
        built = []
        for index, row in enumerate(rows):
            missing = [field for field in _FIELDS if field not in row]
            if missing:
                msg = f'retired row {index} lacks {missing[0]!r}: give it {", ".join(_FIELDS)}'
                raise ValueError(msg)
            built.append(Retired(str(row['name']), str(row['replacement']), str(row['note'])))
        return cls(built)

    def __iter__(self) -> Iterator[Retired]:
        """The rows, in registration order."""
        return iter(self._rows.values())

    def __len__(self) -> int:
        """How many names are retired."""
        return len(self._rows)

    def get(self, name: str) -> Retired | None:
        """The row retiring *name*, or ``None`` when it is live."""
        return self._rows.get(name)

    def refuse(self, name: str, *, where: str = '') -> None:
        """Pass a live *name*; refuse a retired one.

        Raises:
            RetiredName: *name* is retired; the message (prefixed by *where*) names its replacement.

        """
        row = self._rows.get(name)
        if row is not None:
            raise RetiredName(f'{where}: {row.door()}' if where else row.door())

    def found_in(self, text: str) -> list[Retired]:
        """Every retired spelling *text* carries, in registration order (flags as whole tokens)."""
        return [self._rows[name] for name, pattern in self._patterns.items() if pattern.search(text)]
