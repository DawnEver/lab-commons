"""The report mechanism owns client navigation: one JSON config, one packaged script, a narrow producer hook."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from lab_commons.html import NAVIGATION_ID, html_document, navigation_script

CONFIG = {
    'mount': 'sidebar',
    'status': 'current-item',
    'label': 'Groups and cells',
    'title': 'Choose a cell',
    'page_size': 2,
    'orders': [{'id': 'pattern', 'label': 'Pattern first'}, {'id': 'transposition', 'label': 'Transposition first'}],
}


def _config_block(document: str) -> dict:
    match = re.search(rf'<script type="application/json" id="{NAVIGATION_ID}">(.*?)</script>', document)
    assert match is not None
    return json.loads(match.group(1))


def test_navigation_embeds_its_config_and_runs_before_the_producer_script() -> None:
    document = html_document('t', '<div id="sidebar"></div>', javascript='producerHook();', navigation=CONFIG)
    assert _config_block(document) == CONFIG
    script = document[document.index('<script>') :]
    assert script.index('LabNavigation') < script.index('producerHook();')


def test_without_navigation_the_document_carries_no_navigation() -> None:
    document = html_document('t', '<p>x</p>', javascript='a();')
    assert NAVIGATION_ID not in document
    assert 'LabNavigation' not in document


def test_the_packaged_script_is_the_one_embedded() -> None:
    assert navigation_script() in html_document('t', '', navigation=CONFIG)


def test_navigation_id_cannot_be_claimed_by_producer_data() -> None:
    with pytest.raises(ValueError, match=NAVIGATION_ID):
        html_document('t', '', json_data={NAVIGATION_ID: {}}, navigation=CONFIG)


def test_optional_keys_are_accepted() -> None:
    config = {**CONFIG, 'orders': CONFIG['orders'][:1], 'scroll_to': 'review', 'collapse_below_px': 900}
    assert _config_block(html_document('t', '', navigation=config)) == config


@pytest.mark.parametrize(
    'change',
    [
        {'mount': 'bad id'},
        {'status': ''},
        {'scroll_to': '1x'},
        {'page_size': 0},
        {'page_size': True},
        {'page_size': '100'},
        {'collapse_below_px': -1},
        {'orders': []},
        {'orders': [{'id': 'a', 'label': 'A'}, {'id': 'a', 'label': 'B'}]},
        {'orders': [{'id': 'a'}]},
        {'orders': [{'id': 'a', 'label': 1}]},
        {'title': None},
        {'unknown': 1},
    ],
)
def test_malformed_config_is_refused(change) -> None:
    with pytest.raises(ValueError, match='navigation'):
        html_document('t', '', navigation={**CONFIG, **change})


@pytest.mark.parametrize('missing', ['mount', 'status', 'label', 'title', 'page_size', 'orders'])
def test_required_keys_are_required(missing) -> None:
    with pytest.raises(ValueError, match='navigation'):
        html_document('t', '', navigation={k: v for k, v in CONFIG.items() if k != missing})


def test_config_text_cannot_close_the_script_element() -> None:
    document = html_document('t', '', navigation={**CONFIG, 'title': '</script><script>alert(1)'})
    assert '</script><script>alert' not in document
    assert _config_block(document)['title'] == '</script><script>alert(1)'


def test_the_runtime_needs_no_js_engine() -> None:
    """User ruling 2026-10-28: Node is a development dependency; the runtime only ships JS text."""
    code = (
        'import sys; import lab_commons.html as h; h.navigation_script(); '
        "bad=[m for m in sys.modules if m.split('.')[0] in ('nodejs_wheel','py_mini_racer','quickjs','dev_lab')]; "
        'assert not bad, bad'
    )
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True)
