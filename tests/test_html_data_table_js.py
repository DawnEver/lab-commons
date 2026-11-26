"""Behavior of the packaged data-table script's pure core, run on the family's declared JS engine.

Same engine policy as ``test_html_navigation_js.py``: strict xfails until ``dev_lab.dev.jsrun`` is in
this lock, never a host ``node``.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from lab_commons.html import data_table_script

_WAITING = importlib.util.find_spec('dev_lab.dev.jsrun') is None
pytestmark = pytest.mark.xfail(
    _WAITING, reason='waiting on dev_lab.dev.jsrun (dev-lab pr/js-tests-run-on-a-declared-engine)', strict=True
)


def _run(body: str) -> object:
    from dev_lab.dev.jsrun import run_js  # noqa: PLC0415 -- the helper may not exist yet

    return json.loads(run_js(data_table_script() + '\n' + body))


def test_numbers_sort_by_value_text_after_and_blanks_last_both_ways() -> None:
    out = _run(
        "const xs=['1,234','-','b','9','A','','-0.5'];"
        'const by=d=>[...xs].sort((a,b)=>LabDataTable.compare(LabDataTable.key(a),LabDataTable.key(b),d));'
        'process.stdout.write(JSON.stringify([by(false),by(true)]));'
    )
    assert out == [['-0.5', '9', '1,234', 'A', 'b', '-', ''], ['b', 'A', '1,234', '9', '-0.5', '-', '']]


def test_filter_terms_and_together_case_insensitively() -> None:
    out = _run(
        'const m=LabDataTable.matches;'
        "process.stdout.write(JSON.stringify([m('48S8P 4L T1i','t1i 48s'),m('48S8P 4L T1i','t1i 6l'),m('x','  ')]));"
    )
    assert out == [True, False, True]
