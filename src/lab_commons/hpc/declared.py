"""A KIT READS ONLY A DECLARATION IT KNOWS -- an unknown ``[tool.lab_commons.<table>]`` is refused, never read as empty.

THE DEFECT (live 2026-10-12, integrate/main 4e394b6). The cluster served from a lab-commons checkout
older than a table the commit declared. It read the commit's pyproject, found no rows -- an absent
table reads as empty -- and handed nothing to windows. Nothing was wrong
with the declaration; the READER was too old to know it, and said nothing.

THE DECLARATION IDENTIFIES ITSELF BY ITS TABLE NAMES. :data:`KNOWN_TABLES` is every table this kit
reads, and :func:`kit_tables` refuses a pyproject declaring any other, naming the remedy: the reader
is older than the declaration, so update the serving checkout. No version stamp is bumped by hand: a
new table IS the new schema, and a kit that predates it cannot mistake it for absence.

ONE LIST, PINNED BOTH WAYS: ``tests/test_hpc_declared.py`` derives the set of ``tool.lab_commons.<x>``
spellings in ``src/`` and requires it to equal :data:`KNOWN_TABLES`, so a reader added without its row
reds there, and a row whose reader is gone reds too. Every reader of a commit being JUDGED -- the need
vocabulary (:func:`lab_commons.hpc.needsets.read_vocabulary`), the cluster build
(:func:`lab_commons.hpc.builds.read_builds`) and the part producer (:mod:`lab_commons.dev.platformparts`) --
reads through here.

THE KIT ITSELF IS THE EXCEPTION: no host serves a kit newer than the merged one, so a table the kit's
own commit declares before it merges is that commit's defect (:class:`KitAheadOfItsServer`), refused on it.

A RETIRED TABLE IS REFUSED THE OTHER WAY ROUND: the declaration is older than the kit, so the message
names the table's replacement (:data:`lab_commons.hpc.retired.RETIRED`), never "update the kit".

Standard library only, like the rest of :mod:`lab_commons.hpc`.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path
from typing import Any, Final

import lab_commons.launch as _door
from lab_commons.hpc.retired import RETIRED

__all__ = [
    'KNOWN_TABLES',
    'REFUSALS',
    'InvalidDeclaration',
    'KitAheadOfItsServer',
    'OutdatedKit',
    'RetiredTable',
    'kit_tables',
    'pyproject_at',
]

_GIT: Final = shutil.which('git') or 'git'

#: Every ``[tool.lab_commons.<table>]`` this kit reads.
KNOWN_TABLES: Final = frozenset(
    {
        'areas',
        'artefacts',
        'branchset',
        'carry',
        'ci',
        'components',
        'dep',
        'doors',
        'ephemeral_refs',
        'forge',
        'hooks',
        'hpc',
        'integrator',
        'parts',
        'placement',
        'requests',
        'review',
        'schedule',
        'stoprun',
        'tests',
        'tools',
        'verify',
        'worktrees',
    }
)


class OutdatedKit(ValueError):
    """A declaration names a table this kit does not know: the reader is older than what it reads."""


class RetiredTable(ValueError):
    """A declaration names a table this kit retired: the declaration is older than its reader."""


class InvalidDeclaration(ValueError):
    """A declaration this kit reads and refuses: its fields contradict each other or the kit's domain."""


class KitAheadOfItsServer(InvalidDeclaration):
    """The KIT's own commit declares a table its server does not know: the commit's defect, not the reader's.

    A consumer's unknown table waits for a newer serving kit (:class:`OutdatedKit`). The kit's cannot: every
    host serves an already-merged kit, so a table the kit's unmerged commit adds is unknown to all of them
    until it merges, and waiting would leave the PR unclaimed fleet-wide (lab-commons #244, 2026-10-21).
    """


#: The ``[project] name`` of the kit itself, PEP 503-normalised.
_KIT: Final = 'lab-commons'


def _normalised(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


#: Every refusal of a repo's own declaration: a per-repo step reports the repo and skips it, never crashes.
REFUSALS: Final = (RetiredTable, OutdatedKit, InvalidDeclaration)


def kit_tables(text: str) -> dict[str, Any]:
    """The ``[tool.lab_commons]`` tables of pyproject *text*; a table this kit does not know is refused.

    Raises:
        RetiredTable: the text declares a retired table; the message names its replacement.
        OutdatedKit: the text declares a table outside :data:`KNOWN_TABLES`; the message names the remedy.
        KitAheadOfItsServer: the kit's OWN pyproject does; the message names the remedy at the commit.

    """
    data = tomllib.loads(text)
    declared: dict[str, Any] = data.get('tool', {}).get('lab_commons', {})
    retired = [_spelled(name) for name in sorted(declared) if _spelled(name) in RETIRED]
    if retired:
        raise RetiredTable('; '.join(f'{table} is retired: {RETIRED[table]}' for table in retired))
    if unknown := sorted(set(declared) - KNOWN_TABLES):
        tables = ', '.join(f'[tool.lab_commons.{name}]' for name in unknown)
        if _normalised(str(data.get('project', {}).get('name', ''))) == _KIT:
            msg = f'drop {tables} until the kit merges: no host serving lab-commons knows it before then'
            raise KitAheadOfItsServer(msg)
        msg = (
            f'this lab-commons does not know {tables}: it is older than the declaration it reads, and an '
            f'unknown table is never read as empty -- update the serving checkout of lab-commons'
        )
        raise OutdatedKit(msg)
    return declared


def _spelled(name: str) -> str:
    return f'[tool.lab_commons.{name}]'


def pyproject_at(repo: Path, sha: str) -> str:
    """The pyproject text commit *sha* of *repo* carries -- the declaration it is judged by; ``''`` when it has none."""

    def git(*args: str) -> str:
        done = _door.run([_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True)
        return done.stdout

    return (
        git('show', f'{sha}:pyproject.toml')
        if git('ls-tree', '--name-only', sha, '--', 'pyproject.toml').strip()
        else ''
    )
