"""lab_commons.extras: one selection of extras per need-set, refused by name when undeclared."""

import pytest

from lab_commons.extras import declared, extras_for, sync_command

AVAILABLE = {'dev': ['pytest'], 'learn': ['torch'], 'gui': ['pyside6']}


def manifest(dep: dict | None, available: dict | None = None) -> dict:
    out: dict = {'project': {'optional-dependencies': AVAILABLE if available is None else available}}
    if dep is not None:
        out['tool'] = {'dev_lab': {'dep': dep}}
    return out


def test_a_part_needing_nothing_heavy_installs_only_the_declared_extras() -> None:
    m = manifest({'extras': ['dev'], 'needs': {'learn': 'learn'}})
    assert extras_for(m, ()) == ('dev',)
    assert sync_command(m) == 'uv sync --frozen --extra dev'


def test_a_need_adds_its_extra_and_an_unmapped_need_adds_nothing() -> None:
    m = manifest({'extras': ['dev'], 'needs': {'learn': 'learn'}})
    assert extras_for(m, ('windows', 'learn')) == ('dev', 'learn')
    assert (
        sync_command(m, ('learn',), project=False) == 'uv sync --frozen --extra dev --extra learn --no-install-project'
    )


def test_every_heavy_extra_stays_selectable_through_its_need() -> None:
    m = manifest({'extras': [], 'needs': {'learn': 'learn', 'gui': 'gui'}})
    assert extras_for(m, ('gui', 'learn')) == ('gui', 'learn')


def test_optional_dependencies_without_declared_extras_are_refused_by_name() -> None:
    with pytest.raises(ValueError, match=r'no \[tool.dev_lab.dep\] extras'):
        declared(manifest(None))


def test_the_retired_install_everything_switch_is_refused() -> None:
    with pytest.raises(ValueError, match='all is retired'):
        declared(manifest({'extras': ['dev'], 'all': True}))


def test_a_need_mapped_to_an_undeclared_extra_is_refused() -> None:
    with pytest.raises(ValueError, match='no declared extra'):
        declared(manifest({'extras': ['dev'], 'needs': {'cad': 'img-to-cad'}}))


def test_a_repo_without_optional_dependencies_needs_no_table() -> None:
    assert sync_command(manifest(None, available={})) == 'uv sync --frozen'
