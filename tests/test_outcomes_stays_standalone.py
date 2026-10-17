"""A0.8: ``lab_commons.outcomes`` imports only the standard library, and ``lab_commons`` does not import it.

Every engine depends on this vocabulary, so it must cost a consumer nothing else: no third-party
package and no other ``lab_commons`` subpackage. Checked by IMPORTING it in a fresh interpreter whose
finder refuses every other top-level name, and refuses every ``lab_commons.*`` outside the package.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]

_GUARD: Final = """
import importlib, importlib.abc, sys
OWN = sys.argv[2]
class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        top = name.split('.')[0]
        if top == 'lab_commons':
            if name != 'lab_commons' and not (name == OWN or name.startswith(OWN + '.')):
                raise ModuleNotFoundError(f'{OWN} imported {name!r}')
        elif top not in sys.stdlib_module_names:
            raise ModuleNotFoundError(f'{OWN} imported third-party {name!r}')
        return None
sys.meta_path.insert(0, Refuse())
importlib.import_module(sys.argv[1])
print(sorted(m for m in sys.modules if m.startswith('lab_commons.')))
"""


def _import(module: str, own: str = 'lab_commons.outcomes') -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-c', _GUARD, module, own],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        timeout=120,
        cwd=_ROOT,
    )


def test_outcomes_imports_only_the_standard_library_and_itself() -> None:
    done = _import('lab_commons.outcomes')
    assert done.returncode == 0, done.stderr
    assert 'lab_commons.outcomes.record' in done.stdout


def test_importing_lab_commons_does_not_import_outcomes() -> None:
    done = _import('lab_commons')
    assert done.returncode == 0, done.stderr
    assert 'lab_commons.outcomes' not in done.stdout, done.stdout


def test_planted_control_a_foreign_import_is_caught() -> None:
    """PLANTED CONTROL: the same guard, aimed at a module that imports another subpackage, reds."""
    done = _import('lab_commons.box', own='lab_commons.box')
    assert done.returncode != 0
    assert 'imported' in done.stderr
