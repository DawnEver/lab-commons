"""CAPABILITIES: an axis beside the OS -- an engine names its own part, a host capability only filters hosts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.dev.platformparts import split
from lab_commons.hpc.platforms import Table, assign, cannot_run, read_table, runnable

PYPROJECT = """
[tool.lab_commons.platforms]
com = ["windows"]

[tool.lab_commons.capabilities]
jmag = { engine = true }
femm = { engine = true }
dev_checkout = { on = ["windows", "macos"] }
"""


def _can(table: Table, markers: dict[str, set[str]]) -> dict[str, list[str]]:
    """``runnable`` over ids whose markers are given, collecting the way a pytest ``-m`` would."""

    def carries(node: str, expr: str) -> bool:
        if expr.startswith('not '):
            return expr.removeprefix('not ') not in markers[node]
        return bool(markers[node] & set(expr.split(' or ')))

    cannot = {part: {n for n in markers if carries(n, e)} for part in table.parts if (e := cannot_run(table, part))}
    return runnable(list(markers), cannot)


def test_the_capability_axis_is_read_beside_the_os_axis() -> None:
    table = read_table(PYPROJECT)
    assert table.rows == {'com': ('windows',), 'dev_checkout': ('windows', 'macos')}
    assert table.engines == ('femm', 'jmag')
    assert table.hosts == ('dev_checkout',)
    assert table.parts == ('linux', 'windows', 'macos', 'femm', 'jmag')
    assert cannot_run(table, 'jmag') == 'not jmag'
    assert cannot_run(table, 'linux') == 'com or dev_checkout or femm or jmag', 'an engine id is never an OS part'


def test_an_engine_id_goes_to_its_engine_part_and_two_engines_go_nowhere() -> None:
    can = _can(
        read_table(PYPROJECT),
        {'t::j': {'jmag'}, 't::jc': {'jmag', 'com'}, 't::two': {'jmag', 'femm'}, 't::plain': set(), 't::c': {'com'}},
    )
    assert {node: assign(c) for node, c in can.items()} == {
        't::j': 'jmag',
        't::jc': 'jmag',
        't::two': None,
        't::plain': 'linux',
        't::c': 'windows',
    }


def test_a_dev_checkout_id_never_lands_on_the_cluster_part() -> None:
    """The cluster case (coordinator ruling 2026-10-12): the linux part needs no host capability."""
    can = _can(read_table(PYPROJECT), {'t::dev': {'dev_checkout'}, 't::plain': set()})
    linux, _handed = split(can, 'linux')
    assert linux == ['t::plain']
    assert assign(can['t::dev']) == 'windows'


@pytest.mark.parametrize(
    ('text', 'match'),
    [
        ('[tool.lab_commons.platforms]\ndev_checkout = ["windows"]\n', r'move it to \[tool.lab_commons.capabilities\]'),
        ('[tool.lab_commons.platforms]\nnode = ["windows"]\n', r'move it to \[tool.lab_commons.capabilities\]'),
        ('[tool.lab_commons.capabilities]\njmag = { engine = true, on = ["windows"] }\n', 'exactly one of'),
        ('[tool.lab_commons.capabilities]\nlinux = { engine = true }\n', 'names an OS'),
        ('[tool.lab_commons.capabilities]\nnode = { on = ["win32"] }\n', 'drawn from'),
        (
            '[tool.lab_commons.platforms]\nx = ["windows"]\n[tool.lab_commons.capabilities]\nx = { on = ["linux"] }\n',
            'both',
        ),
    ],
)
def test_a_malformed_or_misplaced_row_is_refused_naming_the_fix(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        read_table(text)


def test_this_repo_declares_its_host_capabilities_on_the_capability_axis() -> None:
    table = read_table((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text(encoding='utf-8'))
    assert table.hosts == ('dev_checkout', 'node')
    assert table.engines == ()
    assert cannot_run(table, 'linux') == 'dev_checkout or node'
