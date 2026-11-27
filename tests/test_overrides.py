"""``--set section.key=value``: one override grammar for every CLI, resolved over schema defaults (v1 D125 F5).

A value is a TOML literal when it reads as one (``3``, ``true``, ``[1, 2]``, ``"x"``), else the text itself, so
``--set mesh.size=0.5 mm`` (the shell already stripped the quotes) is the string ``"0.5 mm"`` that the unit door
reads later. A path must name a key the defaults declare; the refusal lists what is settable there.
"""

from __future__ import annotations

import argparse

import pytest

from lab_commons.overrides import Override, OverrideRefused, add_set_argument, apply, fragment, parse_set

DEFAULTS = {'mesh': {'size': '1 mm', 'only_mesh': False, 'order': 2}, 'solve': {'tolerance': 1e-8}}


def test_a_token_parses_to_a_path_and_a_toml_value() -> None:
    assert parse_set('mesh.order=3') == Override(('mesh', 'order'), 3)
    assert parse_set('mesh.only_mesh=true') == Override(('mesh', 'only_mesh'), value=True)
    assert parse_set('solve.tolerance=1e-9') == Override(('solve', 'tolerance'), 1e-9)
    assert parse_set('mesh.size=0.5 mm') == Override(('mesh', 'size'), '0.5 mm')
    assert parse_set('mesh.size="0.5 mm"') == Override(('mesh', 'size'), '0.5 mm')
    assert parse_set('a.b.c=[1, 2]') == Override(('a', 'b', 'c'), [1, 2])


@pytest.mark.parametrize('token', ['mesh', 'mesh=3', '.order=3', 'mesh..order=3', 'mesh.order', 'mesh.or der=3'])
def test_a_malformed_token_is_refused_with_the_grammar(token: str) -> None:
    with pytest.raises(OverrideRefused, match=r'SECTION\.KEY=VALUE'):
        parse_set(token)


def test_overrides_resolve_over_the_defaults_last_one_wins() -> None:
    sets = [parse_set('mesh.order=3'), parse_set('mesh.size=0.5 mm'), parse_set('mesh.order=4')]
    resolved = apply(DEFAULTS, sets)
    assert resolved == {'mesh': {'size': '0.5 mm', 'only_mesh': False, 'order': 4}, 'solve': {'tolerance': 1e-8}}
    assert DEFAULTS['mesh']['order'] == 2  # PLANTED CONTROL: the defaults are never mutated


def test_an_undeclared_key_is_refused_naming_what_is_settable() -> None:
    with pytest.raises(OverrideRefused, match=r'mesh\.deg is not a setting: \[mesh\] declares only_mesh, order, size'):
        apply(DEFAULTS, [parse_set('mesh.deg=3')])
    with pytest.raises(OverrideRefused, match=r'nosuch is not a setting: the top level declares mesh, solve'):
        apply(DEFAULTS, [parse_set('nosuch.key=3')])


def test_a_table_is_never_replaced_by_a_scalar() -> None:
    with pytest.raises(OverrideRefused, match=r'mesh is a table: set one of its keys, such as mesh\.only_mesh=<value>'):
        apply({'mesh': DEFAULTS['mesh'], 'x': {'mesh': {'a': 1}}}, [Override(('mesh',), 3)])


def test_the_fragment_is_the_sparse_nested_table() -> None:
    assert fragment([parse_set('mesh.order=3'), parse_set('solve.tolerance=1e-9')]) == {
        'mesh': {'order': 3},
        'solve': {'tolerance': 1e-9},
    }


def test_every_cli_declares_the_flag_through_one_helper() -> None:
    parser = argparse.ArgumentParser(prog='tool')
    add_set_argument(parser)
    args = parser.parse_args(['--set', 'mesh.order=3', '--set', 'mesh.size=2 mm'])
    assert args.set == [Override(('mesh', 'order'), 3), Override(('mesh', 'size'), '2 mm')]
    assert parser.parse_args([]).set == []
    with pytest.raises(SystemExit):
        parser.parse_args(['--set', 'broken'])
