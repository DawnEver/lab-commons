"""``lab_commons.hpc`` is an opt-in import and reaches nothing outside the standard library and tier 1.

Two properties, asserted against the real artefacts: a fresh interpreter importing ``lab_commons`` does
not load the tier, and no module in it imports a third-party root -- so it needs no extra, on a
workstation or on a login node whose venv carries nothing but this package.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

#: What hpc may import from its own package: itself and the stdlib-only tier-1 modules -- never an opt-in tier.
TIER_ONE = ('lab_commons.hpc', 'lab_commons.log', 'lab_commons.resources', 'lab_commons.width')

HPC_ROOT = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons' / 'hpc'


def test_importing_the_top_level_does_not_load_hpc() -> None:
    probe = 'import sys, lab_commons; print(any(m.startswith("lab_commons.hpc") for m in sys.modules))'
    done = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, encoding='utf-8', check=True)
    assert done.stdout.strip() == 'False'


def test_hpc_imports_only_the_standard_library_and_tier_one() -> None:
    modules = sorted(HPC_ROOT.glob('*.py'))
    assert modules, 'no hpc module was read -- an empty scan is not a clean one'
    foreign = set()
    for source in modules:
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split('.')[0]
                if root not in sys.stdlib_module_names and not name.startswith(TIER_ONE):
                    foreign.add(f'{source.name}: {name}')
    assert foreign == set()
