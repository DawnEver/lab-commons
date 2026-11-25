"""The sortable/filterable data table: escaped by default, trusted markup only by explicit opt-in."""

from __future__ import annotations

import pytest

from lab_commons.html import Cell, Column, Markup, data_table, data_table_css, data_table_script, html_document


def test_cells_and_headers_are_escaped_by_default() -> None:
    out = data_table(['<b>h</b>'], [['<script>x</script>'], [1234]])
    assert '<script>x</script>' not in out
    assert '&lt;script&gt;x&lt;/script&gt;' in out
    assert '&lt;b&gt;h&lt;/b&gt;' in out
    assert '<td>1234</td>' in out


def test_markup_is_the_only_way_to_embed_html() -> None:
    out = data_table(['cell'], [[Markup('<a href="r/x.html">x</a>')]])
    assert '<a href="r/x.html">x</a>' in out


def test_markup_refuses_non_strings() -> None:
    with pytest.raises(TypeError):
        Markup(42)  # type: ignore[arg-type]


def test_column_and_cell_classes_and_titles_are_escaped_attributes() -> None:
    out = data_table(
        [Column('n', title='a "quoted" <tip>', css_class='num vary')],
        [[Cell('7', css_class='set', title='x"y')]],
    )
    assert '<th class="num vary" title="a &quot;quoted&quot; &lt;tip&gt;">n</th>' in out
    assert '<td class="set" title="x&quot;y">7</td>' in out


def test_class_names_must_be_plain_tokens() -> None:
    with pytest.raises(ValueError, match='class'):
        Column('n', css_class='a" onclick="x')
    with pytest.raises(ValueError, match='class'):
        Cell('n', css_class='<b>')


def test_rows_must_match_the_column_count() -> None:
    with pytest.raises(ValueError, match='row 1 has 1 cells, expected 2'):
        data_table(['a', 'b'], [['1', '2'], ['3']])


def test_table_is_marked_for_the_script_and_the_filter_can_be_disabled() -> None:
    on = data_table(['a'], [['1']], caption='Cap <x>')
    off = data_table(['a'], [['1']], filterable=False, table_id='t-1')
    assert '<table data-lab-table' in on
    assert '<caption>Cap &lt;x&gt;</caption>' in on
    assert 'data-lab-filter="off"' in off
    assert 'id="t-1"' in off
    with pytest.raises(ValueError, match='identity'):
        data_table(['a'], [['1']], table_id='bad id')


def test_none_renders_as_dash_and_the_script_and_css_are_packaged() -> None:
    assert '<td>-</td>' in data_table(['a'], [[None]])
    assert 'LabDataTable' in data_table_script()
    assert 'aria-sort' in data_table_css()


def test_html_document_wires_tables_on_request() -> None:
    page = html_document('t', data_table(['a'], [['1']]), data_tables=True)
    assert 'LabDataTable' in page
    assert 'lab-table-filter' in page
    assert 'LabDataTable' not in html_document('t', '<p>x</p>')
