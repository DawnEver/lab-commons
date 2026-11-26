"""TABLE EXPORT: csv and xlsx from typed columns, beside :mod:`lab_commons.viz` and :mod:`lab_commons.html` (D125 F6).

A :class:`Table` is its :class:`Column` declarations and its rows, CHECKED when it is built: each row has one cell
per column, each cell is of its column's kind (``str``, ``int``, ``float`` -- an ``int`` is a ``float`` -- or
``bool``, which is never a number), a float is finite, and ``None`` is allowed only in an ``optional`` column. So a
written file never carries a string in a number column or a row one cell short, and a refusal names the row and
column. A number column may name its unit, and the header says it: ``speed [rad/s]``; the values are written as given
(SI, by the family rule), never converted here.

:func:`write_csv` writes RFC 4180 csv (UTF-8, floats by ``repr`` so they round-trip, booleans ``true``/``false``,
``None`` empty). :func:`write_xlsx` writes one SpreadsheetML sheet in a zip with the standard library alone --
numbers as numbers, booleans as booleans, text inline -- so exporting a table installs and imports nothing heavy.
"""

from __future__ import annotations

import csv
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.sax.saxutils import escape, quoteattr

__all__ = ['Column', 'Table', 'TableRefused', 'write_csv', 'write_xlsx']

_KINDS: Final = (str, int, float, bool)
#: Excel's limit on a sheet name's length.
_SHEET_NAME_MAX: Final = 31


class TableRefused(ValueError):
    """A table whose rows do not fit its columns; the message names the row and the column."""


@dataclass(frozen=True)
class Column:
    """One column: its *name*, its *kind*, the *unit* its numbers are in (``''``: none), and whether it may be empty."""

    name: str
    kind: type
    unit: str = ''
    optional: bool = False

    def __post_init__(self) -> None:
        """Refuse a kind no file format here can type."""
        if self.kind not in _KINDS:
            msg = f'column {self.name!r} kind {self.kind!r} is not one of str, int, float, bool'
            raise TableRefused(msg)

    @property
    def header(self) -> str:
        """The header cell: the name, and the unit in brackets when there is one."""
        return f'{self.name} [{self.unit}]' if self.unit else self.name


def _fits(column: Column, value: object) -> str:
    """Why *value* does not fit *column* (``''``: it fits)."""
    if value is None:
        return '' if column.optional else 'is empty, and the column is not optional'
    kind = type(value)
    if kind is bool and column.kind is not bool:
        return f'is bool, not {column.kind.__name__}'
    if column.kind is float and kind is int:
        return ''
    if not isinstance(value, column.kind):
        return f'is {kind.__name__}, not {column.kind.__name__}'
    if column.kind is float and not math.isfinite(value):  # type: ignore[arg-type]
        return 'is not finite'
    return ''


@dataclass(frozen=True)
class Table:
    """Typed *columns* and their *rows*, checked on construction."""

    columns: tuple[Column, ...]
    rows: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        """Refuse a row whose length or a cell whose kind does not fit the columns.

        Raises:
            TableRefused: naming the row and the column.

        """
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                msg = f'row {index} has {len(row)} cells for {len(self.columns)} columns'
                raise TableRefused(msg)
            for column, value in zip(self.columns, row, strict=True):
                why = _fits(column, value)
                if why:
                    msg = f'row {index} column {column.name!r} {why}'
                    raise TableRefused(msg)


def _text(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return repr(value) if isinstance(value, float) else str(value)


def write_csv(table: Table, path: Path) -> Path:
    """Write *table* to *path* as UTF-8 csv, the header first; return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([column.header for column in table.columns])
        writer.writerows([_text(value) for value in row] for row in table.rows)
    return path


def _ref(row: int, column: int) -> str:
    letters = ''
    column += 1
    while column:
        column, rest = divmod(column - 1, 26)
        letters = chr(65 + rest) + letters
    return f'{letters}{row + 1}'


def _cell(row: int, column: int, value: object) -> str:
    ref = _ref(row, column)
    if value is None:
        return ''
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value!r}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _rows(table: Table) -> str:
    lines = [[column.header for column in table.columns], *table.rows]
    return ''.join(
        f'<row r="{r + 1}">' + ''.join(_cell(r, c, value) for c, value in enumerate(row)) + '</row>'
        for r, row in enumerate(lines)
    )


_MAIN: Final = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_RELS: Final = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_PKG: Final = 'http://schemas.openxmlformats.org/package/2006'


def _parts(sheet: str, rows: str) -> dict[str, str]:
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    doc = f'{_RELS}/officeDocument'
    return {
        '[Content_Types].xml': (
            f'{xml}<Types xmlns="{_PKG}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        '_rels/.rels': (
            f'{xml}<Relationships xmlns="{_PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{doc}" Target="xl/workbook.xml"/></Relationships>'
        ),
        'xl/workbook.xml': (
            f'{xml}<workbook xmlns="{_MAIN}" xmlns:r="{_RELS}"><sheets>'
            f'<sheet name={quoteattr(sheet)} sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        'xl/_rels/workbook.xml.rels': (
            f'{xml}<Relationships xmlns="{_PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{_RELS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        'xl/worksheets/sheet1.xml': f'{xml}<worksheet xmlns="{_MAIN}"><sheetData>{rows}</sheetData></worksheet>',
    }


def write_xlsx(table: Table, path: Path, *, sheet: str = 'table') -> Path:
    """Write *table* to *path* as a one-sheet xlsx named *sheet*, the header first; return *path*.

    Raises:
        TableRefused: *sheet* is empty, too long, or holds a character Excel forbids in a sheet name.

    """
    if not sheet or len(sheet) > _SHEET_NAME_MAX or any(ch in sheet for ch in '[]:*?/\\'):
        msg = f'sheet name {sheet!r} must be 1-{_SHEET_NAME_MAX} characters, none of []:*?/\\'
        raise TableRefused(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as book:
        for name, text in _parts(sheet, _rows(table)).items():
            book.writestr(name, text)
    return path
