"""ONE NEED VOCABULARY -- a test names the RESOURCES it needs by marker, and its part is its NEED-SET.

Design: one admission for the distributed compute system, migration step 3 (2026-10-13). Every resource is
equal: a tool (``femm``, ``jmag``, ``matlab``), an OS (``windows``, ``linux``) or a host fact
(``workstation``) is one name, and a test that needs it carries its marker. The repository declares which
of its markers name a resource, once, with the ONE producer of a part::

    [tool.lab_commons.tests]
    resources = ["femm", "jmag", "windows"]                   # markers naming a resource
    produce = ["-m", "lab_commons.dev.verify", "--part", "{part}"]   # argv after the venv python

PARTS ARE DERIVED, NEVER DECLARED. A test's need-set is the declared resources whose markers it carries;
the suite is partitioned by need-set and each distinct need-set is ONE part, its status context
``tests/<part>`` named by :func:`part_name`: the resources sorted and joined by ``+`` (``femm+windows``),
or :data:`BASE` for the empty set. A new tool is a new marker in ``resources`` and nothing else. Which
host may produce a part is the admission's (:func:`lab_commons.compute.admission.admit`: needs present),
never a table here.

A part's tests are selected by ONE marker expression (:func:`selecting`) -- exactly its need-set, every
other resource excluded -- so a producer needs no id list and a part's selection is a pure function of
the commit's declaration and the part's name.

The declaration is read AT THE COMMIT being judged (``git show <sha>:pyproject.toml``). Standard library
only, like the rest of :mod:`lab_commons.hpc`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from lab_commons.box.machine import RESOURCE_NAME
from lab_commons.hpc.declared import OutdatedKit, kit_tables, pyproject_at

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping
    from pathlib import Path

__all__ = [
    'BASE',
    'PART_NAME',
    'Vocabulary',
    'declared_at',
    'need_sets',
    'needs_of',
    'part_name',
    'read_vocabulary',
    'scarce_parts',
    'selecting',
    'vocabulary_at',
]

#: The part of the tests that need no declared resource.
BASE: Final = 'base'


#: The spelling of a part: :data:`BASE`, or resource names sorted and joined by ``+``.
PART_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*(?:\+[a-z][a-z0-9_]*)*$')

_TABLE: Final = '[tool.lab_commons.tests]'


@dataclass(frozen=True)
class Vocabulary:
    """The resource markers one commit declares, sorted."""

    resources: tuple[str, ...] = ()
    #: The resources a scarce host holds (``scarce``, a subset): a PR needs such a part only where its diff reaches it.
    scarce: tuple[str, ...] = ()


def part_name(needs: Iterable[str]) -> str:
    """The part a need-set is: its names sorted and joined by ``+``, or :data:`BASE` when empty."""
    return '+'.join(sorted(set(needs))) or BASE


def needs_of(part: str) -> frozenset[str]:
    """The need-set *part* names -- the inverse of :func:`part_name`; a malformed name is refused."""
    if not PART_NAME.match(part):
        msg = f'{part!r} is not a part name: {BASE}, or resource names sorted and joined by +'
        raise ValueError(msg)
    if part == BASE:
        return frozenset()
    names = part.split('+')
    if BASE in names or names != sorted(set(names)):
        msg = f'{part!r} is not a part name: its resources are distinct, sorted, and never {BASE!r}'
        raise ValueError(msg)
    return frozenset(names)


def scarce_parts(parts: Iterable[str], scarce: Collection[str]) -> frozenset[str]:
    """The parts of *parts* whose need-set holds a *scarce* resource (``[tool.lab_commons.tests] scarce``)."""
    return frozenset(part for part in parts if needs_of(part) & set(scarce))


def read_vocabulary(text: str) -> Vocabulary:
    """The resource markers pyproject *text* declares; absent is none, a malformed list is refused naming the fix."""
    value = kit_tables(text).get('tests', {}).get('resources', [])
    if not isinstance(value, list) or not all(isinstance(n, str) and RESOURCE_NAME.match(n) for n in value):
        msg = f'{_TABLE} resources = {value!r}: a list of marker names ({RESOURCE_NAME.pattern})'
        raise ValueError(msg)
    if BASE in value or len(set(value)) != len(value):
        msg = f'{_TABLE} resources = {value!r}: distinct names, and never {BASE!r} (the part of no resource)'
        raise ValueError(msg)
    scarce = kit_tables(text).get('tests', {}).get('scarce', [])
    if not isinstance(scarce, list) or not set(scarce) <= set(value) or len(set(scarce)) != len(scarce):
        msg = (
            f'{_TABLE} scarce = {scarce!r}: distinct names among its resources {sorted(value)} '
            '(lab_commons.dev.forgescarce)'
        )
        raise ValueError(msg)
    return Vocabulary(tuple(sorted(value)), tuple(sorted(scarce)))


def vocabulary_at(repo: Path, sha: str) -> Vocabulary:
    """The vocabulary commit *sha* of *repo* declares; a commit carrying no pyproject declares none."""
    return read_vocabulary(pyproject_at(repo, sha))


def declared_at(repo: Path, sha: str) -> tuple[Vocabulary, str]:
    """``(vocabulary, '')`` at *sha*, or ``(empty, why)`` when that sha's OWN declaration is refused.

    A refusal (a malformed row, a retired table, unparsable TOML) belongs to the ONE sha carrying it:
    a reader ranging over many shas refuses that candidate by name and serves the rest.

    Raises:
        OutdatedKit: *sha* declares a table this kit does not know -- the READER is older, not the sha wrong.

    """
    try:
        return vocabulary_at(repo, sha), ''
    except OutdatedKit as why:
        msg = f'declaration at {sha[:12]} refused: {why}'
        raise OutdatedKit(msg) from why
    except ValueError as why:  # RetiredTable and TOMLDecodeError are ValueErrors
        return Vocabulary(), f'declaration at {sha[:12]} refused: {why}'


def selecting(vocabulary: Vocabulary, part: str) -> str:
    """The marker expression selecting exactly *part*'s tests; ``''`` when there is nothing to tell apart.

    A part naming a resource the vocabulary does not declare is refused: no test can carry it.
    """
    needs = needs_of(part)
    if unknown := sorted(needs - set(vocabulary.resources)):
        msg = f'part {part!r} needs {unknown}, which {_TABLE} resources does not declare'
        raise ValueError(msg)
    terms = [name if name in needs else f'not {name}' for name in vocabulary.resources]
    return ' and '.join(terms)


def need_sets(ids: Iterable[str], carrying: Mapping[str, Collection[str]]) -> dict[str, str]:
    """``{id: its part}`` -- *carrying* maps each declared resource to the ids carrying its marker."""
    return {node: part_name(name for name, carried in carrying.items() if node in carried) for node in ids}
