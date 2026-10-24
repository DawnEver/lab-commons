"""lab-commons never reaches the development system: no ``dev`` import, no ``dev_lab`` import or launch.

``box``, ``compute``, ``hpc`` and every top-level module STAY in lab-commons (user ruling
2026-10-22); ``dev`` moved to dev-lab, which depends on lab-commons. A module here that imports or launches
the dev system would make the split a cycle, so every reach is refused -- a module-level import, a lazy one
inside a function, one embedded in a string that runs as ``python -c`` on another host (``hpc/shell.py``'s
environment facts did exactly that), and a ``-m`` launch (the forge verbs ``hpc deploy``, ``tick`` and
``allocate`` launched, which is why they moved to dev-lab). Prose naming a dev-lab module is not a reach.

The scan is textual on purpose: a string-embedded import is never seen by an import graph. The retired
registries are exempt by path -- each is a table of spellings, never an import.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_PKG: Final = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

#: A table of retired spellings, not code: it names old imports so a refusal can quote them.
_SPELLING_TABLES: Final = frozenset({'hpc/retired.py', 'hpc/_retired_compute.py'})

#: A reach of the dev system in any spelling: ``import lab_commons.dev``, ``from lab_commons.dev[.x] import``,
#: ``from lab_commons import dev``, a relative ``from ..dev`` / ``from .dev``, an import of ``dev_lab``, and a
#: ``'-m', 'dev_lab...'`` or ``'-m', 'lab_commons.dev...'`` launch.
_DEV_IMPORT: Final = re.compile(
    r'\bimport\s+lab_commons\.dev\b|\bfrom\s+lab_commons\.dev\b|\bfrom\s+lab_commons\s+import\s+[^\n]*\bdev\b'
    r'|\bfrom\s+\.+dev\b|\bimport\s+dev_lab\b|\bfrom\s+dev_lab\b'
    r"|'-m',\s*'(?:dev_lab|lab_commons\.dev)\b"
)

#: Measured 2026-10-24: 96 staying modules once supervise left. A floor, so an empty scan is not a pass.
_FLOOR: Final = 80


def dev_imports(text: str) -> list[int]:
    """The 1-based lines of *text* that reach the dev system in any spelling."""
    return [n for n, line in enumerate(text.splitlines(), 1) if _DEV_IMPORT.search(line)]


def staying_modules() -> list[Path]:
    """Every tracked-shape ``.py`` under the package except the spelling tables."""
    return sorted(
        p
        for p in _PKG.rglob('*.py')
        if '__pycache__' not in p.parts and p.relative_to(_PKG).as_posix() not in _SPELLING_TABLES
    )


def test_a_planted_dev_reach_is_seen_in_every_spelling() -> None:
    planted = (
        'from lab_commons.dev.forgework import Client\n'
        "    'import json; from lab_commons.dev import forge as k'\n"
        'import lab_commons.dev.forge\n'
        'from lab_commons import box, dev\n'
        'from ..dev import x\n'
        'reached through its CLI, never by importing ``lab_commons.dev``\n'
        'see :mod:`lab_commons.dev.forge`\n'
        'from dev_lab.dev import forge\n'
        "    argv = [sys.executable, '-m', 'dev_lab.dev.forge', 'serve']\n"
        "    argv = [sys.executable, '-m', 'lab_commons.dev.forgealarm', 'run']\n"
        'the forge (:mod:`dev_lab.dev.forge`) records it\n'
    )
    assert dev_imports(planted) == [1, 2, 3, 4, 5, 8, 9, 10]


def test_no_staying_module_reaches_dev() -> None:
    modules = staying_modules()
    assert len(modules) >= _FLOOR, f'FLOOR: {len(modules)} staying modules scanned, under {_FLOOR}'
    found = [
        f'{p.relative_to(_PKG).as_posix()}:{n}' for p in modules for n in dev_imports(p.read_text(encoding='utf-8'))
    ]
    assert not found, 'lab-commons reaches the dev system (dev lives in dev-lab):\n' + '\n'.join(found)
