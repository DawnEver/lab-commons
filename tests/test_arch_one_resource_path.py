"""ONE RESOURCE PATH (P1): nothing builds a Supply or calls the rule but the engine and the providers' measures.

A :class:`~lab_commons.compute.model.Supply` built inline beside an ``admit`` call was the second path the design
deletes (``box/ledger.py`` ``_decide``, ``box/order.py`` ``fits``, ``compute/probe.py``): each was one more place a
supply could be invented instead of measured. So the set of FUNCTIONS that construct ``Supply(`` or call ``admit(``
is pinned BY NAME (NAMED-SETS-NOT-COUNTS): the engine's rule and its view of a scope, and each provider's measure.
A new site reds naming itself; a site that stops reading reds too, so the pin never outlives what it pins.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

SRC: Final = Path(__file__).resolve().parents[1] / 'src' / 'lab_commons'

#: ``(module, function)`` -- every place a Supply is built or the rule is called.
ALLOWED: Final = frozenset(
    {
        ('lab_commons.resources.engine', 'admits'),  # THE RULE: the one door onto compute.admission.admit
        ('lab_commons.resources.engine', 'Engine.supply'),  # every provider's measure on a scope, summed
        ('lab_commons.resources.engine', 'Engine.available'),  # a scope's supply less what the ledger holds
        ('lab_commons.box.providers', 'measured'),  # the box's measure on a reading already taken (ledger, queue)
        ('lab_commons.box.providers', 'size'),  # the box's whole size: what can never fit
        ('lab_commons.box.providers', 'BoxOS.measure'),
        ('lab_commons.box.providers', 'ToolSeats.measure'),
        ('lab_commons.box.providers', 'Lent.measure'),
        ('lab_commons.hpc.provider', 'SlurmAccount.measure'),
        ('lab_commons.hpc.provider', 'unmeasured'),  # an account Slurm did not answer for: nothing
    }
)

_CALLED: Final = frozenset({'Supply', 'admit'})


def _name(func: ast.expr) -> str:
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''


def sites(tree: ast.Module, module: str) -> set[tuple[str, str]]:
    """``(module, qualified function)`` of every ``Supply(...)`` or ``admit(...)`` call in *tree*."""
    found: set[tuple[str, str]] = set()

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                walk(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Call) and _name(child.func) in _CALLED:
                found.add((module, '.'.join(scope) or '<module>'))
            walk(child, scope)

    walk(tree, ())
    return found


def _scan() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob('*.py')):
        parts = path.relative_to(SRC.parent).with_suffix('').parts
        module = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
        found |= sites(ast.parse(path.read_text(encoding='utf-8')), module)
    return found


def test_only_the_engine_and_the_providers_measures_build_a_supply_or_call_the_rule() -> None:
    assert _scan() == ALLOWED


def test_the_scan_finds_an_inline_supply_and_admit_planted_control() -> None:
    planted = ast.parse(
        'def _decide(free):\n'
        '    return admit(job, demand, Supply("box", free))\n'
        'class Box:\n'
        '    def fits(self):\n'
        '        return compute.model.Supply("box", x)\n'
    )
    assert sites(planted, 'm') == {('m', '_decide'), ('m', 'Box.fits')}
