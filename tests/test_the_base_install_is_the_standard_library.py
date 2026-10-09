"""The base install is the standard library, and every gate still needs its extra.

Every module outside the declared gates imports with NO third-party package importable.

A consumer that wants process reaping or a config path must not inherit an array library. The proof
is not a reading of import lines but an IMPORT, in a fresh interpreter whose finder refuses every
top-level name outside ``sys.stdlib_module_names`` -- so a lazy import inside a function is allowed
(it is paid only by its caller), and a module-level one anywhere in the chain is caught.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]
_PKG: Final = _ROOT / 'src' / 'lab_commons'

#: The modules that need an extra, and which one. Two-sided: a gated module that imports cleanly
#: without its extra is a stale gate and reds as surely as an ungated one that does not.
GATED: Final[dict[str, str]] = {
    'em': 'units',
    'file_io': 'io',
    'multiprocess': 'units',
    'paths': 'paths',
    'structured': 'structured',
    'units': 'units',
    'viz': 'viz',
}

_BLOCKER: Final = """
import importlib.abc, sys
STD = set(sys.stdlib_module_names) | {'lab_commons', '_pytest'}
class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split('.')[0] not in STD:
            raise ModuleNotFoundError(f'third-party {name!r} is not part of the base install')
        return None
sys.meta_path.insert(0, Refuse())
import importlib
importlib.import_module(sys.argv[1])
"""


def _modules() -> tuple[str, ...]:
    names = {p.stem for p in _PKG.glob('*.py') if not p.stem.startswith('_') or p.stem == '__init__'}
    names |= {p.parent.name for p in _PKG.glob('*/__init__.py')}
    names.discard('__init__')
    return tuple(sorted(names))


def _imports_bare(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-c', _BLOCKER, f'lab_commons.{module}'],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=_ROOT,
    )


def test_the_walk_is_not_vacuous() -> None:
    """A floor on the population, so a walk that found nothing cannot read as a clean one."""
    assert len(_modules()) >= 15, _modules()


def test_every_ungated_module_imports_with_no_third_party_package() -> None:
    failures = {
        m: r.stderr.strip().splitlines()[-1] for m in _modules() if m not in GATED if (r := _imports_bare(m)).returncode
    }
    assert not failures, (
        f'these modules pull a third-party package at import: {failures}. Make the import lazy at its one '
        f'caller, or gate the module behind the extra that installs it (and add it to GATED).'
    )


def test_every_gate_still_needs_its_extra() -> None:
    stale = [m for m in GATED if _imports_bare(m).returncode == 0]
    assert not stale, f'{stale} import with no third-party package: drop them from GATED in this edit'


def test_the_blocker_refuses_a_planted_third_party_import() -> None:
    """THE PLANTED CONTROL: the same blocker, on a module that is gated for a reason."""
    assert _imports_bare('units').returncode != 0
