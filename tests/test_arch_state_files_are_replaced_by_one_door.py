"""ONE DOOR REPLACES A STATE FILE: :func:`lab_commons.compute.durable.durable_write` (temp, fsync, ``os.replace``).

INCIDENT 2026-10-20: a hand-written rewrite (``write_text``) of ``compute-learned/<repo>.json`` was torn to
5746 NUL bytes when the box's Python was deleted mid-write, and every run died at admission. A site that
stages its own temp file and renames it is a second, unfsynced copy of the door; this scan refuses one.
Append-only logs (``os.fsync`` after an append, with a torn-tail repair) are a different shape and pass.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_PKG: Final = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

DOOR: Final = 'compute/durable.py'

#: Modules that stage-and-rename by hand, each with the reason the door cannot serve it.
EXEMPT: Final[dict[str, str]] = {
    '_records.py': 'lock records: publish is link-or-replace that must report a contested rename',
    'hpc/collect.py': 'shipped to the cluster as a standalone stdlib-only script',
}

_STAGED: Final = ('staged', 'temp', 'tmp', 'part', 'temporary')

#: Re-measured 2026-10-25: 98 modules after supervise left for dev-lab. A floor, so an empty scan is not a pass.
SOURCE_FLOOR: Final = 80


def hand_rolled(tree: ast.Module) -> list[int]:
    """Lines calling ``<staged-name>.replace(<target>)`` -- a temp file renamed over a state file by hand."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'replace'
        and len(node.args) == 1
        and isinstance(node.func.value, ast.Name)
        and any(word in node.func.value.id.lower() for word in _STAGED)
    ]


def test_no_module_but_the_door_replaces_a_state_file_by_hand() -> None:
    modules = sorted(p for p in _PKG.rglob('*.py') if '__pycache__' not in p.parts)
    assert len(modules) >= SOURCE_FLOOR, f'{len(modules)} modules scanned, below the floor {SOURCE_FLOOR}'
    found = {
        rel: lines
        for path in modules
        if (lines := hand_rolled(ast.parse(path.read_text(encoding='utf-8'))))
        and (rel := path.relative_to(_PKG).as_posix())
    }
    raw = {path: lines for path, lines in found.items() if path not in {DOOR, *EXEMPT}}
    assert raw == {}, f'write these through lab_commons.compute.durable.durable_write: {raw}'
    assert sorted(set(EXEMPT) - set(found)) == [], 'an exempt module no longer stages by hand: delete its row'


@pytest.mark.parametrize(
    ('body', 'flagged'),
    [
        ("staged = p.with_name('x.tmp')\nstaged.write_text(t)\nstaged.replace(p)\n", True),
        ("temp = p.with_suffix('.tmp')\ntemp.replace(p)\n", True),
        ('durable_text(p, t)\n', False),
        ("text.replace('a', 'b')\n", False),
        ('path.replace(other)\n', False),
    ],
)
def test_the_scan_flags_a_hand_staged_rename_and_passes_the_door(body: str, flagged: bool) -> None:  # noqa: FBT001
    """PLANTED both ways."""
    assert bool(hand_rolled(ast.parse(body))) is flagged
