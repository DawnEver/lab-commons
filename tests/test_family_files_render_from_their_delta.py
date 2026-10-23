"""Every family project file here is a RENDERING of dev-lab's bases plus :mod:`_famconfig_delta`.

The cut-over to dev-lab hand-edited the rendered stamps; this pins that every artefact still equals
what `python -m dev_lab.dev.famfiles --deltas tests/_famconfig_delta.py` writes, so a hand edit reds.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from dev_lab.dev.famfiles import load_deltas, render_all

_GIT = shutil.which('git') or 'git'
ROOT = Path(__file__).resolve().parents[1]
DELTAS = load_deltas(ROOT / 'tests' / '_famconfig_delta.py')


def test_every_family_file_is_current_against_its_delta() -> None:
    lines = render_all(ROOT, DELTAS, write=False)
    assert lines, 'no artefact was inspected'
    assert [line for line in lines if not line.endswith(': current')] == []


def test_the_agent_auto_mode_file_is_ignored() -> None:
    """`.claude/automode.json` is per-machine agent state; PLANTED CONTROL: `.claude/rules/x.md` is not ignored."""

    def ignored(path: str) -> bool:
        return subprocess.run([_GIT, 'check-ignore', '-q', path], cwd=ROOT, check=False).returncode == 0

    assert ignored('.claude/automode.json')
    assert not ignored('.claude/rules/x.md')
