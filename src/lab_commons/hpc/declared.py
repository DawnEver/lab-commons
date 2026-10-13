"""A KIT READS ONLY A DECLARATION IT KNOWS -- an unknown ``[tool.lab_commons.<table>]`` is refused, never read as empty.

THE DEFECT (live 2026-10-12, integrate/main 4e394b6). The cluster served from a lab-commons checkout
older than the ``[tool.lab_commons.capabilities]`` table. It read the commit's pyproject, found no
capability rows -- an absent table reads as empty -- and handed nothing to windows. Nothing was wrong
with the declaration; the READER was too old to know it, and said nothing.

THE DECLARATION IDENTIFIES ITSELF BY ITS TABLE NAMES. :data:`KNOWN_TABLES` is every table this kit
reads, and :func:`kit_tables` refuses a pyproject declaring any other, naming the remedy: the reader
is older than the declaration, so update the serving checkout. No version stamp is bumped by hand: a
new table IS the new schema, and a kit that predates it cannot mistake it for absence.

ONE LIST, PINNED BOTH WAYS: ``tests/test_hpc_declared.py`` derives the set of ``tool.lab_commons.<x>``
spellings in ``src/`` and requires it to equal :data:`KNOWN_TABLES`, so a reader added without its row
reds there, and a row whose reader is gone reds too. Every reader of a commit being JUDGED -- the part
split (:func:`lab_commons.hpc.platforms.read_table`), the cluster build (:func:`lab_commons.hpc.builds.read_builds`)
and the part producer (:mod:`lab_commons.dev.platformparts`) -- reads through here.

Standard library only, like the rest of :mod:`lab_commons.hpc`.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Final

__all__ = ['KNOWN_TABLES', 'OutdatedKit', 'kit_tables', 'pyproject_at']

_GIT: Final = shutil.which('git') or 'git'

#: Every ``[tool.lab_commons.<table>]`` this kit reads.
KNOWN_TABLES: Final = frozenset(
    {
        'areas',
        'artefacts',
        'branchset',
        'capabilities',
        'carry',
        'ci',
        'dep',
        'doors',
        'forge',
        'hooks',
        'hpc',
        'integrator',
        'parts',
        'placement',
        'platforms',
        'schedule',
        'stoprun',
        'verify',
        'worktrees',
    }
)


class OutdatedKit(ValueError):
    """A declaration names a table this kit does not know: the reader is older than what it reads."""


def kit_tables(text: str) -> dict[str, Any]:
    """The ``[tool.lab_commons]`` tables of pyproject *text*; a table this kit does not know is refused.

    Raises:
        OutdatedKit: the text declares a table outside :data:`KNOWN_TABLES`; the message names the remedy.

    """
    declared: dict[str, Any] = tomllib.loads(text).get('tool', {}).get('lab_commons', {})
    if unknown := sorted(set(declared) - KNOWN_TABLES):
        tables = ', '.join(f'[tool.lab_commons.{name}]' for name in unknown)
        msg = (
            f'this lab-commons does not know {tables}: it is older than the declaration it reads, and an '
            f'unknown table is never read as empty -- update the serving checkout of lab-commons'
        )
        raise OutdatedKit(msg)
    return declared


def pyproject_at(repo: Path, sha: str) -> str:
    """The pyproject text commit *sha* of *repo* carries -- the declaration it is judged by; ``''`` when it has none."""

    def git(*args: str) -> str:
        done = subprocess.run(
            [_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True
        )
        return done.stdout

    return (
        git('show', f'{sha}:pyproject.toml')
        if git('ls-tree', '--name-only', sha, '--', 'pyproject.toml').strip()
        else ''
    )
