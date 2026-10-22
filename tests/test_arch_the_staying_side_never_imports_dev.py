"""The execution side never imports ``lab_commons.dev`` (user ruling 2026-10-22).

``box``, ``compute``, ``hpc``, ``supervise`` and every top-level module STAY in lab-commons; only ``dev``
moves out (to dev-lab). A staying module that imports dev would make the split a cycle, so every import
of it is refused -- a module-level one, a lazy one inside a function, and one embedded in a string that
runs as ``python -c`` on another host (``hpc/shell.py``'s environment facts did exactly that).

The scan is textual on purpose: a string-embedded import is never seen by an import graph. The retired
registries are exempt by path -- each is a table of spellings, never an import.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from _arch_corpus import assert_floor

_PKG: Final = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

#: A table of retired spellings, not code: it names old imports so a refusal can quote them.
_SPELLING_TABLES: Final = frozenset({'hpc/retired.py', 'hpc/_retired_compute.py'})

#: An import of dev in any spelling: ``import lab_commons.dev``, ``from lab_commons.dev[.x] import``,
#: ``from lab_commons import dev``, and a relative ``from ..dev`` / ``from .dev``.
_DEV_IMPORT: Final = re.compile(
    r'\bimport\s+lab_commons\.dev\b|\bfrom\s+lab_commons\.dev\b|\bfrom\s+lab_commons\s+import\s+[^\n]*\bdev\b'
    r'|\bfrom\s+\.+dev\b'
)

#: Measured 2026-10-22: 150+ staying modules. A floor, so an empty scan is not a pass.
_FLOOR: Final = 100


def dev_imports(text: str) -> list[int]:
    """The 1-based lines of *text* that import ``lab_commons.dev`` in any spelling."""
    return [n for n, line in enumerate(text.splitlines(), 1) if _DEV_IMPORT.search(line)]


def staying_modules() -> list[Path]:
    """Every tracked-shape ``.py`` under the package except ``dev/`` and the spelling tables."""
    return sorted(
        p
        for p in _PKG.rglob('*.py')
        if p.relative_to(_PKG).parts[0] != 'dev'
        and '__pycache__' not in p.parts
        and p.relative_to(_PKG).as_posix() not in _SPELLING_TABLES
    )


def test_a_planted_dev_import_is_seen_in_every_spelling() -> None:
    planted = (
        'from lab_commons.dev.forgework import Client\n'
        "    'import json; from lab_commons.dev import forge as k'\n"
        'import lab_commons.dev.forge\n'
        'from lab_commons import box, dev\n'
        'from ..dev import x\n'
        'reached through its CLI, never by importing ``lab_commons.dev``\n'
        'see :mod:`lab_commons.dev.forge`\n'
    )
    assert dev_imports(planted) == [1, 2, 3, 4, 5]


def test_no_staying_module_imports_dev() -> None:
    modules = staying_modules()
    assert_floor(len(modules), _FLOOR, 'staying modules')
    found = [
        f'{p.relative_to(_PKG).as_posix()}:{n}' for p in modules for n in dev_imports(p.read_text(encoding='utf-8'))
    ]
    assert not found, 'the staying side imports lab_commons.dev (dev moves to dev-lab):\n' + '\n'.join(found)
