"""THE MACHINERY under `_collect_census_rows.py`: one row's shape, and the re-measurement that judges it.

Split from the table for the reason `_config_census.py` is: an edit that adds a row must not be able
to relax the check that would have refused it, because the two arrive in one diff and read as one
change. Everything computed here is computed by `lab_commons.dev.collectscope`; this file only joins
a repo to its manifest and its tree, and holds the floors' refusals.

A REPO NOT ON THIS BOX IS ABSENT, NOT FAILING -- repos are cloned per box -- and `REPO_FLOOR` is what
stops that being a free pass. A sibling is read at `HEAD` so a lane's half-finished edit over there
cannot turn THIS repo red; lab-commons answers for its own working tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

from _config_census_rows import REPO_PATHS

from lab_commons.dev.collectscope import FileReading, Reach, local_modules, reach, read_imports, tree_texts
from lab_commons.dev.syncscope import Selection, _base, extras

__all__ = [
    'BASE',
    'HERE',
    'PATHS',
    'WHY_FLOOR',
    'CollectRow',
    'CollectRowError',
    'ReachDriftError',
    'VacuousTreeScanError',
    'assert_reaches',
    'measure',
    'present',
    'readings_of',
]

#: The repo under verification, whose WORKING TREE is the authority for its own row.
HERE: Final = 'lab-commons'

#: Where each repo is checked out relative to the family root. Taken from the config census rather
#: than retyped, so the worktree ruling -- never the motronics MAIN checkout -- is stated once.
PATHS: Final[dict[str, str]] = {HERE: HERE, **REPO_PATHS}

#: The shortest a row's REASON may be. A row's deliverable is WHY that selection is made and what
#: its three sets mean; a caption carries neither, and is how a row is added without anybody looking.
WHY_FLOOR: Final = 200

#: This file is `<lab-commons>/tests/`, so the family sits one level above the repo root.
BASE: Final = Path(__file__).resolve().parents[2]

#: Where a repo keeps its tests. One answer across four repos, asserted rather than assumed: a repo
#: that moved its tree would otherwise read as a tree with no imports, which is a clean-looking lie.
_TESTS: Final = 'tests'


class CollectRowError(ValueError):
    """A row the census refuses to hold -- today, a caption standing in for a reason."""


class ReachDriftError(AssertionError):
    """A recorded selection no longer leaves the test tree what the table records, in either direction."""


class VacuousTreeScanError(AssertionError):
    """A scan read fewer test files, repos or rows than its floor, so its clean answer proves nothing."""


@dataclass(frozen=True, slots=True)
class CollectRow:
    """One repo's selection and what it leaves the TEST TREE able to do. Three sets, all by EQUALITY.

    *unresolved* is RECORDED rather than tolerated: a new import name no committed text can settle is
    a hole in the reader, and it must arrive as an edit somebody looked at.
    """

    repo: str
    selected: tuple[str, ...]
    errors: frozenset[str]
    degrades: frozenset[str]
    unresolved: frozenset[str]
    why: str

    def __post_init__(self) -> None:
        """Refuse a row that cannot be true before any tree is read."""
        if len(self.why) < WHY_FLOOR:
            msg = f'{self.key}: reason is {len(self.why)} characters, below the floor of {WHY_FLOOR}'
            raise CollectRowError(msg)
        if not self.selected:
            msg = f'{self.key}: a row exists where a selection is MADE, and this one names no extra'
            raise CollectRowError(msg)

    @property
    def key(self) -> str:
        """``<repo>[<extra>,<extra>]``, the spelling a refusal names a row by."""
        return f'{self.repo}[{",".join(self.selected)}]'


@cache
def _readings(repo: str, root: Path) -> tuple[dict[str, FileReading], str, frozenset[str], frozenset[str]]:
    """One repo's test-tree readings, manifest, declared set and local names. Cached: the tree is read once.

    MEASURED 2026-09-19: motronics' 2632 test files cost 11 s to read and parse, and the table asks
    two questions of them. A second read would double the census for no second fact.
    """
    texts = tree_texts(root, _TESTS, at_head=repo != HERE)
    manifest = (root / 'pyproject.toml').read_text('utf-8')
    declared = _declared(manifest)
    return ({name: read_imports(name, text) for name, text in texts.items()}, manifest, declared, local_modules(root))


def _declared(manifest: str) -> frozenset[str]:
    """Every distribution this manifest names anywhere -- base, project, and every extra, EXPANDED.

    `_base` is reached by its private name deliberately: the alternative is a second implementation
    of "the project plus its required dependencies" here, free to disagree with the one the scope
    reader uses. A shared private is a seam; a copy is a fork.
    """
    return _base(manifest) | frozenset(dist for members in extras(manifest).values() for dist in members)


def readings_of(repo: str) -> tuple[dict[str, FileReading], str, frozenset[str], frozenset[str]]:
    """One repo's cached readings by NAME, so a caller never rebuilds the root path by hand."""
    return _readings(repo, BASE / PATHS[repo])


def present(repo: str) -> bool:
    """Is this repo checked out on this box? A sibling that is not is ABSENT, never failing."""
    return (BASE / PATHS[repo] / 'pyproject.toml').is_file()


def measure(repo: str, root: Path, selected: tuple[str, ...]) -> Reach:
    """What *selected* leaves *repo*'s test tree able to do. The join, in one call, over the REAL reader."""
    readings, manifest, declared, local = _readings(repo, root)
    return reach(readings, Selection(prunes=True, extras=frozenset(selected)), manifest, declared, local)


def assert_reaches(
    rows: tuple[CollectRow, ...],
    floors: dict[str, int],
    *,
    repo_floor: int,
    row_floor: int,
) -> frozenset[str]:
    """Refuse unless every recorded selection still leaves the tree what the table records.

    Returns the repos it actually read, so a caller can assert the one it runs in was among them.

    Raises:
        ReachDriftError: the join now answers something else, in either direction.
        VacuousTreeScanError: a tree, the repo set or the row set was below its floor.

    """
    seen: set[str] = set()
    checked = 0
    for row in rows:
        if not present(row.repo):
            continue
        found = measure(row.repo, BASE / PATHS[row.repo], row.selected)
        _assert_read(found.files, floors[row.repo], row.repo)
        _assert_row(row, found)
        seen.add(row.repo)
        checked += 1
    if len(seen) < repo_floor or checked < row_floor:
        msg = (
            f'judged {checked} selections across {len(seen)} repos, below the floors of {row_floor} '
            f'and {repo_floor}. A census that reached one tree is not a census, and finding no '
            f'stranded import in a set that was not read proves nothing.'
        )
        raise VacuousTreeScanError(msg)
    return frozenset(seen)


def _assert_read(files: int, floor: int, repo: str) -> None:
    if files < floor:
        msg = (
            f'{repo}: read {files} test files, below the floor of {floor}. An empty stranded set over '
            f'a tree that was not read is vacuous, not green -- re-point the scan at the test tree.'
        )
        raise VacuousTreeScanError(msg)


def _assert_row(row: CollectRow, found: Reach) -> None:
    live = (found.errors, found.degrades, found.unresolved)
    recorded = (row.errors, row.degrades, row.unresolved)
    if live != recorded:
        msg = (
            f'{row.key} records errors={sorted(row.errors)} degrades={sorted(row.degrades)} '
            f'unresolved={sorted(row.unresolved)} and now measures errors={sorted(found.errors)} '
            f'degrades={sorted(found.degrades)} unresolved={sorted(found.unresolved)}. Sites: '
            f'{found.sites[:5]}. Either the manifest moved an extra, or a test grew an import its '
            f'selection cannot supply -- which is a repo that reports no verdict rather than a '
            f'failing one. Re-measure the row; never widen it.'
        )
        raise ReachDriftError(msg)
