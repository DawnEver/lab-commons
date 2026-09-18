r"""THE PUBLISHER DOES NOT ADOPT ITS OWN `.gitignore` BASE, and that is a MEASUREMENT rather than a gap.

WHY THIS MODULE EXISTS. `.gitignore` is RENDERED and three of the four repos carry the stamp; only
lab-commons does not. Everywhere else in this family "the kit is the one repo outside the contract
it publishes" turned out to be a real defect and got closed -- the Makefile on 2026-09-18, the
pre-commit config on 2026-09-17. So the exemption here reads like the next one to close, and it was
argued in PROSE alone: a sentence in `_config_census_rows.py` and a paragraph in `_famconfig_delta`.
Prose is exactly what this family has measured going stale three times. The argument is therefore
re-measured in code, and the answer is ratcheted BOTH WAYS.

WHAT THE MEASUREMENT SAYS, 2026-09-19, over the four live checkouts:

* The kit's file is TEN patterns; the consumers' are 62, 30 and 73, and their three-way core is
  FOURTEEN -- which is exactly what `BASES['.gitignore']` renders.
* The kit holds THREE of those fourteen (`*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`). The
  census row said two of twelve; both numbers were understated and the subset answer did not move.
* ALL SIX pairwise subset tests are FALSE. The kit is a subset of no consumer AND no consumer is a
  subset of the kit. The census arm only ever checked the first of those two directions.
* ELEVEN of the base's fourteen lines are absent from the kit, and they are not stylistic: the base
  floats every cache rule (`**/__pycache__/`) where this tree anchors it (`__pycache__/`), and it
  carries `.coverage*`, `.mypy_cache/` and `uv.lock` for tools this repo does not run.

SO ADOPTION IS A REAL BEHAVIOUR CHANGE AND NOT A STAMP. It would add eleven patterns to a
ten-pattern file, and one of the ten it must keep names this repo alone --
`src/lab_commons/__version__.py`, the file hatch-vcs writes, which the consumers spell
`**/__version__.py` because they have several packages and this one has one. Neither spelling is
portable to the other.

THE RATCHET HAS TWO SIDES AND BOTH ARE HERE, which is the only thing that makes this a pin rather
than a second copy of the prose:

* `TestTheKitsGitignoreIsAdoptableByNobody` reds the day the files converge -- that is the day to
  re-open this, with these numbers quoted. Its planted control writes a `.gitignore` that IS the
  base's rendering and proves the same reader then says "adoptable", so the green above is a
  measurement of the tree and not of a constant.
* `TestNobodyStampsItWhileTheMeasurementStillSaysNot` reds if the file acquires the renderer's stamp
  or a `.gitignore` Delta appears for this repo. Without it, the first class is a one-sided ratchet
  that a single `render()` call could satisfy by making the answer moot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from _config_census import gitignore_patterns, reachable_repos
from _config_census_rows import REPO_PATHS, SHARED_GITIGNORE_CORE
from _famconfig_delta import DELTAS, REPO

from lab_commons.dev.famconfig import BASES, FOREIGN, STAMP, Delta, inspect_file, meaningful_lines, render

#: The artefact under measurement, spelled once.
GITIGNORE: Final = '.gitignore'

#: This checkout's root -- `tests/` sits directly under it.
ROOT: Final = Path(__file__).resolve().parent.parent

#: The kit's three patterns out of the consumers' fourteen, as a NAMED SET. A count cannot say WHICH
#: one moved, and this family has already lowered a digit rather than read a swap.
KIT_SHARE: Final[frozenset[str]] = frozenset({'*.egg-info/', '.pytest_cache/', '.ruff_cache/'})

#: The eleven base lines this tree does not carry, NAMED. When this set empties the base has become
#: adoptable here and the question re-opens; when it changes, a base line moved and the reader is
#: told which. The floor under the reading is that it is non-empty, which the first arm asserts.
UNADOPTED_BASE_LINES: Final[frozenset[str]] = frozenset(
    {
        '**/.env',
        '**/__pycache__/',
        '**/log/*.log',
        '**/log/*.log.error',
        '**/temp/**',
        '*.c',
        '*.spec',
        '.coverage*',
        '.mypy_cache/',
        '.venv*',
        'uv.lock',
    }
)

#: The one pattern in the kit's file that names this repo and cannot be spelled portably. If it ever
#: leaves, a generated version file gets committed and the build stops being reproducible from tags.
KIT_ONLY_VERSION_FILE: Final = 'src/lab_commons/__version__.py'

#: A delta that adds nothing and drops nothing, at a ZERO ceiling -- the declaration this repo
#: actually makes about `.gitignore`. It is spelled once because three arms below ask what the kit's
#: own reader says about an UNDECLARED file, and three copies of it could drift into three questions.
EMPTY_DELTA: Final = Delta(repo=REPO, added=(), dropped={}, ceiling=0)

#: The floor under every reading below. Two consumers is the minimum at which "a subset of none" is
#: a claim about the family rather than about one pair.
CONSUMER_FLOOR: Final = 2


def patterns_of(text: str) -> frozenset[str]:
    """A `.gitignore`'s live patterns from TEXT, through the kit's own line reader.

    IT EXISTS BECAUSE THE CENSUS READER CANNOT READ A PLANTED FILE. `gitignore_patterns` resolves its
    content at git HEAD -- deliberately, since a census is about what the four repos DECLARE -- so
    pointing it at a `tmp_path` returns the empty set rather than what was just written there. The
    control below needs a reader over TEXT, and `TestTheTwoReadersAgree` is what keeps the two from
    being two different definitions of "pattern".
    """
    return frozenset(meaningful_lines(text, BASES[GITIGNORE].comment))


def _reached() -> dict[str, Path]:
    """Every repo of the family checked out on this box, with the kit's presence asserted."""
    reached = reachable_repos(REPO_PATHS)
    assert 'lab-commons' in reached, 'the reader could not find the tree it is running in'
    return reached


class TestTheKitsGitignoreIsAdoptableByNobody:
    """THE ARGUMENT, RE-MEASURED. Every arm reads the live files; none reads the prose that claims it."""

    def test_all_six_pairwise_subset_tests_are_false(self) -> None:
        """BOTH DIRECTIONS, which is what the census arm never checked.

        "The kit is a subset of no consumer" alone would still be true of a kit holding one exotic
        pattern and nothing else -- a file that SHOULD adopt the base. The other direction is what
        rules that out: no consumer is inside the kit either, so neither file can absorb the other.
        """
        reached = _reached()
        kit = gitignore_patterns(reached['lab-commons'])
        consumers = {repo: gitignore_patterns(root) for repo, root in reached.items() if repo != 'lab-commons'}
        assert len(consumers) >= CONSUMER_FLOOR, f'{sorted(consumers)}: fewer than {CONSUMER_FLOOR} consumers reached'
        assert len(kit) >= 8, f'{len(kit)} patterns read from the kit; the reader found nothing'
        for repo, other in sorted(consumers.items()):
            assert other, f'{repo}: no patterns read at all'
            assert not kit <= other, (
                f'the kit is now a SUBSET of {repo}. That is one of the two conditions for adoption; '
                f'check the other arms and re-open this question with the numbers quoted.'
            )
            assert not other <= kit, f'{repo} is now a subset of the kit, so the kit could render for it'

    def test_the_kit_holds_exactly_three_of_the_consumer_core(self) -> None:
        """A NAMED SET rather than a count, so a swap is legible and the digit cannot be edited down."""
        kit = gitignore_patterns(_reached()['lab-commons'])
        assert len(SHARED_GITIGNORE_CORE) >= 10, f'the recorded core is {len(SHARED_GITIGNORE_CORE)} patterns'
        assert kit & set(SHARED_GITIGNORE_CORE) == KIT_SHARE, (
            f'the kit now shares {sorted(kit & set(SHARED_GITIGNORE_CORE))} of the consumer core '
            f'rather than {sorted(KIT_SHARE)}. If it GREW toward the fourteen, adoption is getting '
            f'cheap and this is the arm that says so.'
        )

    def test_eleven_base_lines_are_absent_here_and_they_are_named(self) -> None:
        """THE RE-OPENING TRIGGER. When this set empties, the base has arrived and the answer changes."""
        base = BASES[GITIGNORE]
        kit = gitignore_patterns(_reached()['lab-commons'])
        missing = frozenset(base.content_lines) - kit
        assert missing, (
            'every line of the .gitignore base is now in this tree. The base has become adoptable '
            'here -- re-open the decision, quote this measurement, and adopt rather than deleting '
            'this arm.'
        )
        assert missing == UNADOPTED_BASE_LINES, (
            f'the unadopted base lines moved: now {sorted(missing)}, recorded '
            f'{sorted(UNADOPTED_BASE_LINES)}. A line that left the gap was either adopted here or '
            f'dropped from the base, and those are different edits.'
        )

    def test_the_one_line_that_stays_names_this_repo_and_has_no_portable_spelling(self) -> None:
        """WHAT ADOPTION WOULD HAVE TO PRESERVE, and the reason a rendered file cannot carry it."""
        reached = _reached()
        kit = gitignore_patterns(reached['lab-commons'])
        assert KIT_ONLY_VERSION_FILE in kit, (
            f'{KIT_ONLY_VERSION_FILE} left this .gitignore. hatch-vcs writes that file at build time; '
            f'without the pattern it gets committed and the build stops being reproducible from the tag.'
        )
        assert KIT_ONLY_VERSION_FILE not in set(BASES[GITIGNORE].content_lines), (
            'the base now carries this repo`s version-file path -- that is one repo`s tree layout '
            'legislated from a shared file'
        )
        for repo, root in sorted(reached.items()):
            if repo == 'lab-commons':
                continue
            assert KIT_ONLY_VERSION_FILE not in gitignore_patterns(root), (
                f'{repo} now ignores {KIT_ONLY_VERSION_FILE}, a path that does not exist in it'
            )

    def test_a_planted_gitignore_that_IS_the_base_is_read_as_adoptable(self, tmp_path: Path) -> None:
        """PLANTED CONTROL. Without it the three arms above could be green on a reader that finds nothing.

        The planted file is the base's own rendering, so it is what an ADOPTED tree looks like -- and
        the same helpers must then answer the other way round: nothing missing, and the consumer core
        fully held. This is what makes the greens above a measurement of THIS tree.
        """
        base = BASES[GITIGNORE]
        planted = tmp_path / GITIGNORE
        planted.write_text(render(base, Delta(repo='planted', added=(), dropped={}, ceiling=0)), encoding='utf-8')
        adopted = patterns_of(planted.read_text(encoding='utf-8'))

        assert frozenset(base.content_lines) - adopted == frozenset(), (
            'the control fixture does not even hold the base it was rendered from; it proves nothing'
        )
        assert set(SHARED_GITIGNORE_CORE) <= adopted, sorted(set(SHARED_GITIGNORE_CORE) - adopted)
        assert adopted & set(SHARED_GITIGNORE_CORE) != KIT_SHARE, (
            'an adopted tree and this tree read identically through these helpers -- the arms above '
            'are asserting a constant'
        )


class TestTheTwoReadersAgree:
    """The census reads at HEAD and the planted control reads text. This is what joins them.

    Without this the control could be green against a definition of "pattern" the property arms
    never use -- and it also catches the other thing that would make every arm above describe a file
    nobody has: an UNCOMMITTED edit to this repo's own `.gitignore`.
    """

    def test_the_working_tree_and_HEAD_agree_on_this_repos_gitignore(self) -> None:
        """One file, two readers, and a floor under the reading."""
        at_head = gitignore_patterns(_reached()['lab-commons'])
        in_tree = patterns_of((ROOT / GITIGNORE).read_text(encoding='utf-8'))
        assert at_head, 'the HEAD reader found nothing, so every comparison above is against an empty set'
        assert in_tree == at_head, (
            f'this checkout`s .gitignore differs from HEAD: only-in-tree {sorted(in_tree - at_head)}, '
            f'only-at-HEAD {sorted(at_head - in_tree)}. Every arm in this module reads the COMMITTED '
            f'file, so an uncommitted edit here makes them describe a file nobody else has.'
        )


class TestNobodyStampsItWhileTheMeasurementStillSaysNot:
    """THE OTHER SIDE. A ratchet with one side lets the answer be made moot instead of re-decided."""

    def test_the_kit_declares_no_gitignore_delta(self) -> None:
        """A delta is how adoption is declared here. One appearing without the arms above flipping is the bad half."""
        assert GITIGNORE not in DELTAS, (
            f'a .gitignore Delta arrived for {REPO} while the measurement still says the base is not '
            f'adoptable here. Flip `TestTheKitsGitignoreIsAdoptableByNobody` first -- with numbers -- '
            f'or this is an adoption nobody argued.'
        )
        assert DELTAS, 'the delta table is empty, so this arm would pass against a repo that declared nothing'

    def test_the_file_carries_no_renderer_stamp_and_the_kit_reads_it_as_FOREIGN(self) -> None:
        """Through the kit's OWN reader, so this cannot disagree with what `inspect_file` would say."""
        path = ROOT / GITIGNORE
        assert path.is_file(), f'no {GITIGNORE} at {path}'
        assert STAMP not in path.read_text(encoding='utf-8'), (
            f'this {GITIGNORE} is stamped by lab_commons.dev.famconfig. Either it was rendered -- in '
            f'which case declare the Delta and flip the measurement -- or a stamp was pasted into a '
            f'file nobody rendered, which is the declaration-that-lies shape outright.'
        )
        report = inspect_file(path, BASES[GITIGNORE], EMPTY_DELTA)
        assert report.status == FOREIGN, (
            f'{GITIGNORE} reads as {report.status} rather than FOREIGN: {report.detail}. FOREIGN is '
            f'the honest status for a file this package did not write, and any other status means '
            f'the adoption question has already been answered somewhere else.'
        )

    def test_a_planted_stamp_is_caught_by_the_same_reader(self, tmp_path: Path) -> None:
        """PLANTED CONTROL for this side. A guard that never sees a stamp is not guarding one."""
        path = tmp_path / GITIGNORE
        path.write_text((ROOT / GITIGNORE).read_text(encoding='utf-8'), encoding='utf-8')
        clean = inspect_file(path, BASES[GITIGNORE], EMPTY_DELTA)
        assert clean.status == FOREIGN, f'the unedited copy already reads {clean.status}'

        path.write_text(f'# {STAMP}\n{path.read_text(encoding="utf-8")}', encoding='utf-8')
        bent = inspect_file(path, BASES[GITIGNORE], EMPTY_DELTA)
        assert bent.status != FOREIGN, 'a stamped file still reads FOREIGN -- the stamp check sees nothing'
        assert STAMP in path.read_text(encoding='utf-8'), 'the control planted no stamp, so it convicted nothing'
