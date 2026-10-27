"""An older kit refuses a newer declaration by name, never reading an unknown table as empty."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from lab_commons.hpc.builds import read_builds
from lab_commons.hpc.declared import (
    KNOWN_TABLES,
    InvalidDeclaration,
    KitAheadOfItsServer,
    OutdatedKit,
    RetiredTable,
    kit_tables,
)
from lab_commons.hpc.needsets import read_vocabulary
from lab_commons.hpc.retired import MOVED_TABLES

SRC = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'


def _dev_lab_source() -> Path:
    """dev-lab's package directory, READ as text (never imported): half the readers of these tables live there."""
    spec = importlib.util.find_spec('dev_lab')
    assert spec is not None, 'dev-lab is the dev group here: `uv sync` installs it'
    assert spec.submodule_search_locations, 'dev_lab is a package'
    return Path(next(iter(spec.submodule_search_locations)))


#: A table this kit has never heard of -- what a newer declaration looks like to an older reader.
NEWER = """
[tool.lab_commons.tests]
resources = ["node"]

[tool.lab_commons.tomorrow]
rows = 1
"""


def test_an_unknown_table_is_refused_naming_the_remedy() -> None:
    """PLANTED (live 2026-10-12): the cluster's older kit read no capability rows and handed nothing on."""
    with pytest.raises(OutdatedKit, match=r'\[tool\.lab_commons\.tomorrow\].*update the serving dev-lab checkout'):
        kit_tables(NEWER)


def test_every_judged_reader_refuses_it() -> None:
    for reader in (read_vocabulary, read_builds):
        with pytest.raises(OutdatedKit):
            reader(NEWER)


def test_the_kit_declaring_an_unknown_table_is_its_own_commits_defect() -> None:
    """PLANTED (lab-commons #244): no server of the kit (dev-lab) knows a table its own unmerged commit adds.

    PLANTED CONTROL: the test above -- the same table in a consumer is the READER's defect (OutdatedKit).
    """
    with pytest.raises(
        KitAheadOfItsServer, match=r'drop \[tool\.lab_commons\.tomorrow\] until the kit merges'
    ) as raised:
        kit_tables('[project]\nname = "dev_lab"\n' + NEWER)
    assert not isinstance(raised.value, OutdatedKit)
    assert isinstance(raised.value, InvalidDeclaration)


def test_a_known_declaration_reads_and_an_absent_one_is_empty() -> None:
    """PLANTED CONTROL: the refusal is about an UNKNOWN table, not about any table."""
    assert kit_tables(NEWER.replace('tomorrow', 'tools')).keys() == {'tests', 'tools'}
    assert kit_tables('[project]\nname = "x"\n') == {}


@pytest.mark.parametrize('table', MOVED_TABLES)
def test_a_table_moved_to_dev_lab_is_refused_naming_its_new_home(table: str) -> None:
    """Plan A3 (2026-10-27): no fallback reads the old namespace, so declaring it there is refused, never ignored."""
    assert table not in KNOWN_TABLES
    with pytest.raises(RetiredTable, match=rf'\[tool\.dev_lab\.{table}\]'):
        kit_tables(NEWER.replace('tomorrow', table))


def test_the_known_tables_are_exactly_the_ones_this_kit_spells() -> None:
    """Both sides: a reader added without its row, and a row whose reader is gone -- in either repo.

    The tables are read by lab-commons (``hpc``) and by the dev system split out of it (dev-lab), and both
    read through :func:`kit_tables`, so the one list answers for the readers of both.
    """
    spelled = {
        name
        for root in (SRC, _dev_lab_source())
        for path in root.rglob('*.py')
        if path.name not in {'declared.py', 'retired.py', '_retired_compute.py'}  # the retired tables, by name
        for name in re.findall(r'tool\.lab_commons\.([a-z_]+)', path.read_text(encoding='utf-8'))
    }
    assert len(spelled) >= 5, 'a floor (the five staying tables, plan A3): an empty scan reads exactly like agreement'
    assert spelled == KNOWN_TABLES


def test_this_repos_own_declaration_is_known() -> None:
    kit_tables((SRC.parents[1] / 'pyproject.toml').read_text(encoding='utf-8'))
