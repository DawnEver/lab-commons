"""Only ``lab_commons.outcomes`` defines a task-result type named Outcome, Status or Conclusion (plan A1.10).

A second ``Status`` elsewhere is the drift the one vocabulary exists to end: a reader importing the
wrong one gets a type that looks right and means something else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import lab_commons

OWNED = frozenset({'Outcome', 'Status', 'Conclusion'})
PACKAGE = Path(lab_commons.__file__).parent
HOME = PACKAGE / 'outcomes'


def squatters(source: str) -> list[str]:
    """Every class or alias at any depth that takes one of the owned names."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name in OWNED:
            found.append(node.name)
        elif isinstance(node, ast.Assign):
            found += [t.id for t in node.targets if isinstance(t, ast.Name) and t.id in OWNED]
    return found


def test_the_scan_sees_a_planted_squatter() -> None:
    assert squatters('class Status:\n    pass\n') == ['Status']
    assert squatters('def f():\n    class Outcome: ...\n') == ['Outcome']
    assert squatters('Conclusion = int\n') == ['Conclusion']
    assert squatters('from lab_commons.outcomes import Status\n') == []


def test_only_outcomes_defines_the_result_names() -> None:
    files = [p for p in PACKAGE.rglob('*.py') if HOME not in p.parents]
    assert files, 'FLOOR: the scan read no file'
    offenders = {
        str(p.relative_to(PACKAGE)): names for p in files if (names := squatters(p.read_text(encoding='utf-8')))
    }
    assert offenders == {}
