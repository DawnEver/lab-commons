"""A retired name is a DATA row, refused by name with its replacement (v1 ruling D125 F4).

A product's term and verb tables supply rows -- (name, replacement, migration note) -- and
:class:`lab_commons.retired.Registry` is the one mechanism that refuses them and finds them in text, so no product
reimplements the refusal. ``lab_commons.hpc.retired`` is built on the same registry.
"""

from __future__ import annotations

import importlib

import pytest

from lab_commons.hpc import retired as hpc_retired
from lab_commons.retired import Registry, Retired, RetiredName

ROWS = [
    {'name': 'case run', 'replacement': 'case solve', 'note': 'D90: verbs are stages'},
    {'name': '--mode', 'replacement': '--select', 'note': 'one selector flag'},
    {'name': 'gauss_door', 'replacement': 'runtime/solver_engines', 'note': 'D24'},
]


def test_rows_build_a_registry_and_a_retired_name_is_refused_with_its_door() -> None:
    registry = Registry.of(ROWS)
    with pytest.raises(RetiredName, match=r'`case run` is retired: use case solve \(D90: verbs are stages\)'):
        registry.refuse('case run')
    registry.refuse('case solve')  # PLANTED CONTROL: a live name passes
    assert registry.get('gauss_door') == Retired('gauss_door', 'runtime/solver_engines', 'D24')
    assert registry.get('runtime') is None


def test_a_refusal_names_where_it_was_met() -> None:
    with pytest.raises(RetiredName, match=r'motronics: `case run` is retired'):
        Registry.of(ROWS).refuse('case run', where='motronics')


def test_text_is_scanned_for_retired_spellings_flags_as_whole_tokens() -> None:
    registry = Registry.of(ROWS)
    assert [r.name for r in registry.found_in('see gauss_door and --mode x')] == ['--mode', 'gauss_door']
    assert registry.found_in('--model x') == []  # PLANTED CONTROL: a flag is a whole token


def test_a_row_is_refused_when_incomplete_or_named_twice() -> None:
    with pytest.raises(ValueError, match=r"retired row 0 lacks 'replacement'"):
        Registry.of([{'name': 'x', 'note': 'n'}])
    with pytest.raises(ValueError, match=r"retired name 'x' is registered twice"):
        Registry.of([{'name': 'x', 'replacement': 'y', 'note': ''}, {'name': 'x', 'replacement': 'z', 'note': ''}])


def test_the_kits_own_registry_is_built_on_the_mechanism() -> None:
    assert isinstance(hpc_retired.REGISTRY, Registry)
    spelling = next(iter(hpc_retired.RETIRED))
    assert spelling in hpc_retired.retired_in(f'x {spelling} y')
    assert hpc_retired.refusal('hpc', spelling) == f'`hpc {spelling}` is retired: use {hpc_retired.RETIRED[spelling]}'


@pytest.mark.parametrize('module', ['units', 'em'])
def test_the_pint_pydantic_modules_are_retired_to_the_unit_door(module: str) -> None:
    """v1 rulings D125/D126: one unit door; the spelling is registered and the module is gone, with no alias."""
    spelling = f'lab_commons.{module}'
    assert spelling in hpc_retired.RETIRED
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(spelling)
    assert 'lab_commons.quantity' in hpc_retired.RETIRED[spelling]
