"""An older kit refuses a newer declaration by name, never reading an unknown table as empty."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lab_commons.hpc.builds import read_builds
from lab_commons.hpc.declared import KNOWN_TABLES, OutdatedKit, kit_tables
from lab_commons.hpc.needsets import read_vocabulary

SRC = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

#: A table this kit has never heard of -- what a newer declaration looks like to an older reader.
NEWER = """
[tool.lab_commons.tests]
resources = ["node"]

[tool.lab_commons.tomorrow]
rows = 1
"""


def test_an_unknown_table_is_refused_naming_the_remedy() -> None:
    """PLANTED (live 2026-10-12): the cluster's older kit read no capability rows and handed nothing on."""
    with pytest.raises(OutdatedKit, match=r'\[tool\.lab_commons\.tomorrow\].*update the serving checkout'):
        kit_tables(NEWER)


def test_every_judged_reader_refuses_it() -> None:
    for reader in (read_vocabulary, read_builds):
        with pytest.raises(OutdatedKit):
            reader(NEWER)


def test_a_known_declaration_reads_and_an_absent_one_is_empty() -> None:
    """PLANTED CONTROL: the refusal is about an UNKNOWN table, not about any table."""
    assert kit_tables(NEWER.replace('tomorrow', 'verify')).keys() == {'tests', 'verify'}
    assert kit_tables('[project]\nname = "x"\n') == {}


def test_the_known_tables_are_exactly_the_ones_this_kit_spells() -> None:
    """Both sides: a reader added without its row, and a row whose reader is gone."""
    spelled = {
        name
        for path in SRC.rglob('*.py')
        if path.name not in {'declared.py', 'retired.py'}  # retired.py names the tables it RETIRED
        for name in re.findall(r'tool\.lab_commons\.([a-z_]+)', path.read_text(encoding='utf-8'))
    }
    assert len(spelled) >= 15, 'a floor: an empty scan reads exactly like agreement'
    assert spelled == KNOWN_TABLES


def test_this_repos_own_declaration_is_known() -> None:
    kit_tables((SRC.parents[1] / 'pyproject.toml').read_text(encoding='utf-8'))
