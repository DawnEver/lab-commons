"""Table export: csv and xlsx from TYPED columns, beside viz and html (v1 ruling D125 F6).

A column declares its name, its kind and (for a number) its unit; every row is checked against the columns before a
byte is written, so a sheet never carries a string in a number column or a row one cell short. The xlsx is written
with the standard library (SpreadsheetML in a zip): numbers stay numbers, text is inline, the header names the unit.
"""

from __future__ import annotations

import csv
import math
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from lab_commons.table import Column, Table, TableRefused, write_csv, write_xlsx

_NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

TABLE = Table(
    (Column('point', str), Column('speed', float, 'rad/s'), Column('poles', int), Column('ok', bool)),
    (('A', 314.159, 8, True), ('B, "quoted"', 1e-9, 4, False)),
)


def test_csv_writes_the_header_with_units_and_every_row(tmp_path: Path) -> None:
    path = write_csv(TABLE, tmp_path / 'points.csv')
    with path.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ['point', 'speed [rad/s]', 'poles', 'ok']
    assert rows[1] == ['A', '314.159', '8', 'true']
    assert rows[2] == ['B, "quoted"', '1e-09', '4', 'false']


def _sheet(path: Path) -> list[list[tuple[str | None, str]]]:
    with zipfile.ZipFile(path) as book:
        names = set(book.namelist())
        assert {'[Content_Types].xml', 'xl/workbook.xml', 'xl/worksheets/sheet1.xml'} <= names
        root = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))  # noqa: S314 -- the file this test just wrote
    out = []
    for row in root.iterfind('.//s:row', _NS):
        cells = []
        for cell in row.iterfind('s:c', _NS):
            text = cell.find('.//s:t', _NS) if cell.get('t') == 'inlineStr' else cell.find('s:v', _NS)
            cells.append((cell.get('t'), '' if text is None else (text.text or '')))
        out.append(cells)
    return out


def test_xlsx_keeps_numbers_as_numbers_and_text_inline(tmp_path: Path) -> None:
    rows = _sheet(write_xlsx(TABLE, tmp_path / 'points.xlsx', sheet='points'))
    assert [v for _, v in rows[0]] == ['point', 'speed [rad/s]', 'poles', 'ok']
    kinds = [t for t, _ in rows[1]]
    assert kinds == ['inlineStr', None, None, 'b']
    assert math.isclose(float(rows[1][1][1]), 314.159, rel_tol=0, abs_tol=1e-12)
    assert rows[2][0] == ('inlineStr', 'B, "quoted"')


def test_a_row_that_does_not_fit_its_columns_is_refused_before_writing(tmp_path: Path) -> None:
    """PLANTED CONTROLS: a wrong kind, a short row, a NaN."""
    with pytest.raises(TableRefused, match=r"row 0 column 'speed' is str, not float"):
        Table((Column('speed', float),), (('fast',),))
    with pytest.raises(TableRefused, match=r'row 1 has 1 cells for 2 columns'):
        Table((Column('a', int), Column('b', int)), ((1, 2), (3,)))
    with pytest.raises(TableRefused, match=r"row 0 column 'x' is not finite"):
        Table((Column('x', float),), ((float('nan'),),))
    assert not (tmp_path / 'never.csv').exists()


def test_an_int_is_a_float_but_a_bool_is_not_a_number() -> None:
    Table((Column('x', float),), ((3,),))
    with pytest.raises(TableRefused, match=r"column 'x' is bool, not int"):
        Table((Column('x', int),), ((True,),))


def test_an_optional_column_holds_none_as_an_empty_cell(tmp_path: Path) -> None:
    table = Table((Column('x', float, optional=True),), ((None,), (1.5,)))
    with write_csv(table, tmp_path / 'o.csv').open(encoding='utf-8', newline='') as handle:
        assert list(csv.reader(handle)) == [['x'], [''], ['1.5']]
    assert _sheet(write_xlsx(table, tmp_path / 'o.xlsx'))[1] == []
