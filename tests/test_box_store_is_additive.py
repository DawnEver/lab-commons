"""THE BOX LEDGER'S SCHEMA ONLY GROWS: it is one file shared by every lab-commons version on the machine.

THE INCIDENT, 2026-10-26: a branch renamed the ledger's ``seats`` column to ``amounts`` on open. The verify door's
own claim -- which sits on the REAL ledger, not a test's -- ran that branch's code, the real file was renamed, and
every process on the box still on the published lab-commons failed with ``no such column: seats``.

So every schema change must be ADDITIVE: ``CREATE TABLE IF NOT EXISTS``, or ``ALTER TABLE .. ADD COLUMN`` of a
column an older writer can omit (nullable, or with a default). Anything else -- RENAME, DROP, a rebuilt table -- is
refused here. That is also what makes the verify door safe: whatever tree it runs, the ledger it opens stays
readable and writable by the published version. Each test opens its own ledger (``conftest._a_box_of_its_own``).
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Final

from lab_commons.box import BOX_ENV
from lab_commons.box._store import _ADDED, _SCHEMA, box_path

SRC: Final = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

_DDL: Final = re.compile(r'\b(ALTER|DROP|RENAME|CREATE)\b', re.IGNORECASE)
_CREATE: Final = re.compile(r'^\s*CREATE TABLE IF NOT EXISTS \w+ \(', re.IGNORECASE)
_ADD: Final = re.compile(r'^\s*ALTER TABLE \{?\w+\}? ADD COLUMN \{?\w+\}?\s*$', re.IGNORECASE)
_COLUMN: Final = re.compile(r'^(?P<name>\w+) (?P<type>INTEGER|REAL|TEXT)(?P<rest>.*)$')


def unsafe(statement: str) -> bool:
    """Whether one DDL *statement* could break an older reader: anything but a guarded CREATE or an ADD COLUMN."""
    return not (_CREATE.match(statement) or _ADD.match(statement))


def column_unsafe(spelled: str) -> bool:
    """Whether an added column could break an older WRITER: NOT NULL without a default, or not a plain column."""
    found = _COLUMN.match(spelled)
    return found is None or ('NOT NULL' in found['rest'].upper() and 'DEFAULT' not in found['rest'].upper())


def _ddl_strings() -> list[str]:
    """Every string literal in the box and resources packages that reads as DDL."""
    found = []
    for path in [*sorted((SRC / 'box').glob('*.py')), *sorted((SRC / 'resources').glob('*.py'))]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        pieces = {id(v) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for v in node.values}
        for node in ast.walk(tree):
            plain = isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in pieces
            text = node.value if plain else None
            if isinstance(node, ast.JoinedStr):
                text = ''.join(v.value if isinstance(v, ast.Constant) else '{x}' for v in node.values)
            if text and _DDL.search(text) and re.search(r'\bTABLE\b|\bCOLUMN\b', text, re.IGNORECASE):
                found.append(text)
    return found


def test_every_schema_change_to_the_shared_ledger_is_additive() -> None:
    statements = [one for one in _SCHEMA.split(';') if one.strip()]
    assert statements, 'no schema was read -- an empty scan is not a clean one'
    assert [one for one in statements if unsafe(one)] == []
    assert [spelled for added in _ADDED.values() for _, spelled in added if column_unsafe(spelled)] == []
    ddl = _ddl_strings()
    assert any('ADD COLUMN' in one for one in ddl), 'FLOOR: the forward step itself must be found'
    assert [one for one in ddl if one not in _SCHEMA and unsafe(one)] == []


def test_a_rename_a_drop_and_a_required_column_are_refused_planted_control() -> None:
    assert unsafe('ALTER TABLE claims RENAME COLUMN seats TO amounts')
    assert unsafe('ALTER TABLE tickets DROP COLUMN seats')
    assert unsafe('DROP TABLE claims')
    assert unsafe('CREATE TABLE claims (id INTEGER)'), 'a CREATE without IF NOT EXISTS replaces nothing safely'
    assert column_unsafe('amounts TEXT NOT NULL')
    assert not unsafe('ALTER TABLE {table} ADD COLUMN {spelled}')
    assert not column_unsafe("scope TEXT NOT NULL DEFAULT 'box'")


def test_a_test_never_opens_the_machine_ledger() -> None:
    assert os.environ[BOX_ENV] == str(box_path())
    assert 'lab-commons-box' in str(box_path()), 'each test has its own ledger (conftest)'
