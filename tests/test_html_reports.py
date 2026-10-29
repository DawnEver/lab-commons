"""Case identity owns portable filenames; HTML mechanisms never infer business identity."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def html() -> ModuleType:
    return importlib.import_module('lab_commons.html')


def test_primary_and_secondary_report_names_preserve_identity(html) -> None:
    primary = html.ReportIdentity('case_a')
    assert primary.stem == 'case_a'
    assert primary.filename == 'case_a.html'
    assert html.ReportIdentity('case_a', 'geometry').filename == 'case_a--geometry.html'
    assert html.ReportIdentity('Case 中文.v1').filename == 'Case 中文.v1.html'


@pytest.mark.parametrize(
    'name',
    [
        '',
        ' ',
        '.',
        '..',
        '../case',
        'a/b',
        'a\\b',
        '/case',
        'C:case',
        'a<b',
        'a>b',
        'a"b',
        'a|b',
        'a?b',
        'a*b',
        'case.',
        'case ',
        ' case',
        'a\nb',
        'a\x00b',
        'a\x7fb',
        'CON',
        'con.txt',
        'CONIN$',
        'conout$.txt',
        'NUL .txt',
        'COM1  .log',
        'PRN',
        'AUX',
        'NUL',
        'COM1',
        'lpt9.txt',
        'COM¹',
        'com².txt',
        'LPT³',
        'case.html',
        'case.HTML',
        'case--comparison',
        '-case',
        'case-',
    ],
)
def test_unsafe_or_file_shaped_case_identity_is_refused(html, name) -> None:
    with pytest.raises(ValueError, match='report identity'):
        html.ReportIdentity(name)


@pytest.mark.parametrize('name', [None, 1, Path('case')])
def test_identity_is_a_name_not_a_path_or_coerced_value(html, name) -> None:
    with pytest.raises(TypeError):
        html.ReportIdentity(name)


def test_report_kind_uses_the_same_validation_and_length_limit(html) -> None:
    with pytest.raises(ValueError, match='report identity'):
        html.ReportIdentity('case', '../geometry')
    with pytest.raises(ValueError, match='report identity'):
        html.ReportIdentity('case', '')
    with pytest.raises(ValueError, match='report identity'):
        html.ReportIdentity('case', 'comparison--detail')
    with pytest.raises(ValueError, match='255-byte'):
        html.ReportIdentity('a' * 200, 'b' * 50)
    assert len(html.ReportIdentity('a' * 250).filename.encode('utf-8')) == 255
    with pytest.raises(ValueError, match='255-byte'):
        html.ReportIdentity('a' * 251)
    with pytest.raises(ValueError, match='255-byte'):
        html.ReportIdentity('中' * 84)


def test_primary_and_secondary_identity_composition_has_no_ambiguous_dot_split(html) -> None:
    assert html.ReportIdentity('case.geometry').filename != html.ReportIdentity('case', 'geometry').filename
    assert html.ReportIdentity('case.a', 'b').filename != html.ReportIdentity('case', 'a.b').filename
    assert html.ReportIdentity('case.geometry').filename == 'case.geometry.html'
    assert html.ReportIdentity('case-a', 'detail-b').filename == 'case-a--detail-b.html'


def test_secondary_separator_is_included_in_the_portable_filename_limit(html) -> None:
    assert len(html.ReportIdentity('a' * 200, 'b' * 48).filename.encode('utf-8')) == 255
    with pytest.raises(ValueError, match='255-byte'):
        html.ReportIdentity('a' * 200, 'b' * 49)


@pytest.mark.parametrize(('case', 'kind'), [('case-', 'kind'), ('case', '-kind'), ('case', 'kind-')])
def test_hyphen_boundary_cannot_make_secondary_identity_composition_ambiguous(html, case, kind) -> None:
    with pytest.raises(ValueError, match='report identity'):
        html.ReportIdentity(case, kind)


def test_script_json_roundtrips_without_html_termination(html) -> None:
    payload = {'identity': '</script><img src=x onerror="x">&\\\u2028', 'value': 1}
    encoded = html.script_json(payload)
    assert json.loads(encoded) == payload
    assert '<' not in encoded
    assert '>' not in encoded
    assert '&' not in encoded
    with pytest.raises(ValueError, match='Out of range'):
        html.script_json({'bad': float('nan')})


def test_document_shell_escapes_title_but_keeps_explicit_producer_markup(html) -> None:
    content = html.html_document(
        '<b>Motor & Winding</b>',
        '<main id="content">trusted body</main>',
        css='body{color:black}',
        javascript='globalThis.ready=true;',
        json_data={'drawing-data': {'id': '</script>&'}},
    )
    assert content.lower().count('<!doctype html>') == 1
    assert '<meta charset="utf-8">' in content
    assert 'name="viewport"' in content
    assert '<title>&lt;b&gt;Motor &amp; Winding&lt;/b&gt;</title>' in content
    assert '<main id="content">trusted body</main>' in content
    assert '<style>body{color:black}</style>' in content
    assert '<script>globalThis.ready=true;</script>' in content
    encoded = content.split('id="drawing-data">')[1].split('</script>')[0]
    assert json.loads(encoded) == {'id': '</script>&'}


def test_document_rejects_script_identifier_injection(html) -> None:
    with pytest.raises(ValueError, match='script element identity'):
        html.html_document('title', '', json_data={'bad" onclick="x': {}})


def test_writer_uses_identity_and_utf8_with_no_extra_deliverables(html, tmp_path) -> None:
    directory = tmp_path / 'run'
    identity = html.ReportIdentity('case_中文')
    report = html.write_html_report(directory, identity, 'first\r\n中文')
    assert report == directory / 'case_中文.html'
    assert report.read_bytes() == 'first\n中文'.encode()
    assert list(directory.iterdir()) == [report]
    assert html.write_html_report(directory, identity, 'second') == report
    assert report.read_text(encoding='utf-8') == 'second'


def test_atomic_replace_failure_preserves_existing_report_and_removes_temporary(html, tmp_path, monkeypatch) -> None:
    identity = html.ReportIdentity('case')
    target = html.write_html_report(tmp_path, identity, 'original')

    def refuse_replace(_source, _destination) -> None:
        message = 'simulated destination refusal'
        raise OSError(message)

    monkeypatch.setattr(html.Path, 'replace', refuse_replace)
    with pytest.raises(OSError, match='destination refusal'):
        html.write_html_report(tmp_path, identity, 'replacement')
    assert target.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.iterdir()) == [target]


def test_relative_asset_url_is_portable_encoded_and_confined(html, tmp_path) -> None:
    asset = tmp_path / 'case figures' / 'angle "one" & two.svg'
    assert html.relative_asset_url(asset, tmp_path) == 'case%20figures/angle%20%22one%22%20%26%20two.svg'
    with pytest.raises(ValueError, match='is not in the subpath'):
        html.relative_asset_url(tmp_path.parent / 'outside.svg', tmp_path)


def test_key_value_table_escapes_values_and_retains_parent_paths(html) -> None:
    content = html.key_value_table({'rotor': {'width': '<img src=x>'}, 'stator': {'width': 'a&b'}})
    assert 'rotor.width' in content
    assert 'stator.width' in content
    assert '&lt;img src=x&gt;' in content
    assert 'a&amp;b' in content
    assert '<img' not in content
    assert 'class="responsive-table"' in content
    assert '--min-col-width:200px' in content
    assert 'empty' in html.key_value_table({'empty': {}})
    assert '&lt;key&gt;' in html.key_value_table({'<key>': 'value'})


@pytest.mark.parametrize('width', [0, -1, True, '200; color:red'])
def test_table_width_cannot_inject_css(html, width) -> None:
    with pytest.raises((TypeError, ValueError)):
        html.key_value_table({}, min_col_width=width)


def test_recursive_mapping_is_refused_instead_of_recursing_forever(html) -> None:
    values = {}
    values['loop'] = values
    with pytest.raises(ValueError, match='recursive'):
        html.key_value_table(values)


def test_html_mechanism_imports_without_plotting_or_business_dependencies() -> None:
    code = """
import importlib.abc
import sys
class RefuseHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy','matplotlib','bokeh','downstream_pkg'}:
            raise RuntimeError('unexpected dependency: '+fullname)
sys.meta_path.insert(0, RefuseHeavy())
from lab_commons.html import ReportIdentity, html_document
assert ReportIdentity('case').filename == 'case.html'
assert '<title>Case</title>' in html_document('Case','')
"""
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True)
