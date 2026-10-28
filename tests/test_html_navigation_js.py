"""Behaviour of the packaged navigation script's pure core, run on the family's declared JS engine.

WAITING ON dev-lab's ``dev_lab.dev.jsrun`` (branch pr/js-tests-run-on-a-declared-engine): until it is in
this lock these are strict xfails; once it lands the condition turns false and they must pass. Never a
host ``node`` (user ruling 2026-10-28: Node is a development dependency only).
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from lab_commons.html import navigation_script

_WAITING = importlib.util.find_spec('dev_lab.dev.jsrun') is None
pytestmark = pytest.mark.xfail(
    _WAITING, reason='waiting on dev_lab.dev.jsrun (dev-lab pr/js-tests-run-on-a-declared-engine)', strict=True
)


def _run(body: str) -> object:
    from dev_lab.dev.jsrun import run_js  # noqa: PLC0415 -- the helper may not exist yet

    return json.loads(run_js(navigation_script() + '\n' + body))


def test_index_groups_every_item_per_order_preserving_first_seen_order() -> None:
    out = _run(
        "const items=[{id:'a',p:1,t:0},{id:'b',p:0,t:0},{id:'c',p:1,t:1}];"
        "const i=LabNavigation.index(items,['p','t'],(item,o)=>item[o]);"
        'process.stdout.write(JSON.stringify({p:i.groupKeys.p,t:i.groupKeys.t,'
        "pc:i.groups.p.get(1).map(x=>x.id),pos:i.positions.t.get('c'),gp:i.groupPositions.p.get(0)}));"
    )
    assert out == {'p': [1, 0], 't': [0, 1], 'pc': ['a', 'c'], 'pos': 0, 'gp': 1}


def test_index_refuses_duplicate_or_missing_identity() -> None:
    out = _run(
        'const r=[];for(const items of [[{id:"a"},{id:"a"}],[{id:1}]]){'
        'try{LabNavigation.index(items,["x"],()=>0);r.push("ok");}catch(e){r.push(e.message);}}'
        'process.stdout.write(JSON.stringify(r));'
    )
    assert out == ['Duplicate or invalid item identity'] * 2


def test_page_is_bounded_and_refuses_outside_coverage() -> None:
    out = _run(
        'const xs=[1,2,3,4,5],r=[LabNavigation.page(xs,0,2),LabNavigation.page(xs,2,2),LabNavigation.page([],0,2)];'
        'for(const n of [3,-1,0.5])try{LabNavigation.page(xs,n,2);r.push("ok");}catch(e){r.push(e.message);}'
        'process.stdout.write(JSON.stringify(r));'
    )
    assert out == [[1, 2], [5], [], *['Navigation page outside coverage'] * 3]
