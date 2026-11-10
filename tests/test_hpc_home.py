"""The cluster home as facts: the quota read off Ada's real output, the thresholds, the managed roots, the census.

Fixtures are MEASURED on Ada 2026-11-06 (``tests/_hpc_fixtures/ada-*``): ``mmlsquota -Y`` exits 19 there while
printing the right row, so only the rows are trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc import home
from lab_commons.hpc.home import MANAGED, QUOTA, STRAY_FILES, Consumer, Thresholds

FIXTURES: Final = Path(__file__).parent / '_hpc_fixtures'


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding='utf-8')


def test_ada_mmlsquota_y_is_read_against_the_soft_quota_that_binds() -> None:
    """3,032,158 files of a 4,000,000 soft quota (4,040,000 hard): the soft one binds once grace is out."""
    text = f'@@@ fs dss2fs01\n{_fixture("ada-mmlsquota-Y-2026-11-06.txt")}@@@ quota-w\n'
    usage = home.parse_usage(text)
    assert usage is not None
    assert (usage.files, usage.files_limit) == (3_032_158, 4_000_000)
    assert usage.used == 115_084_416 * 1024
    assert usage.limit == 1_048_576_000 * 1024
    assert usage.binding() == 'files'
    assert usage.fraction() == pytest.approx(3_032_158 / 4_000_000)


def test_the_human_mmlsquota_is_the_fallback_and_agrees_with_the_y_form() -> None:
    usage = home.parse_usage(f'@@@ fs dss2fs01\n{_fixture("ada-mmlsquota-2026-11-06.txt")}@@@ quota-w\n')
    assert usage is not None
    assert (usage.files, usage.files_limit) == (3_055_042, 4_000_000)


def test_linux_quota_is_the_last_reader_and_nothing_answering_is_none_never_zero() -> None:
    linux = '@@@ fs /dev/sda1\n@@@ quota-w\nDisk quotas for user u\n  /dev/sda1  100  1000  2000  10  50  60\n'
    usage = home.parse_usage(linux)
    assert usage is not None
    assert (usage.used, usage.files, usage.files_limit) == (100 * 1024, 10, 50)
    assert home.parse_usage('@@@ fs x\n@@@ quota-w\n') is None


def test_a_y_row_on_the_home_filesystem_wins_over_another() -> None:
    header, row = _fixture('ada-mmlsquota-Y-2026-11-06.txt').splitlines()
    other = row.replace('dss2fs01', 'scratchfs').replace(':3032158:', ':1:')
    usage = home.parse_usage(f'@@@ fs /dev/dss2fs01\n{header}\n{other}\n{row}\n@@@ quota-w\n')
    assert usage is not None
    assert usage.files == 3_032_158, 'the home filesystem row, not the first row'


def test_the_thresholds_are_one_declaration_and_ordered() -> None:
    assert 0 < QUOTA.soft < QUOTA.hard < 1
    with pytest.raises(ValueError, match='soft < hard'):
        Thresholds(soft=0.9, hard=0.8)


def test_the_census_is_capped_per_directory_and_stops_counting() -> None:
    text = f'{home.CENSUS_PREFIX}ab\t51\n{home.CENSUS_PREFIX}ci\t51\n{home.CENSUS_PREFIX}u70\t185\nnoise\n'
    found = home.parse_census(text, cap=51)
    assert [c.name for c in found] == ['u70', 'ab', 'ci']
    assert found[1] == Consumer('ab', 51, capped=True)
    assert found[1].text() == '~/ab: >= 51 files'
    assert 'head -n 51' in home.census_script(51), 'the walk stops at the cap: a full du took > 280 s on Ada'


def test_a_stray_is_an_unowned_directory_past_the_limit_and_a_managed_root_never_is() -> None:
    """Planted control: an unowned directory AT the limit is not a stray; one file more is."""
    consumers = [
        Consumer('ab', 11, capped=True),
        Consumer('at-limit', 10, capped=False),
        Consumer('ci', 11, capped=True),
        Consumer('src', 11, capped=True),
    ]
    assert [c.name for c in home.strays(consumers, managed=('src',), limit=10)] == ['ab']
    assert [c.name for c in home.strays(consumers, limit=10)] == ['ab', 'src'], 'a deploy root is passed by its owner'
    assert 'ci' in MANAGED
    assert STRAY_FILES > 23_000, 'one worktree of a large repository is not yet a stray'


def test_a_stray_names_its_rule_and_both_doors() -> None:
    (line,) = home.stray_text([Consumer('ab', 50_001, capped=True)])
    assert line.startswith('STRAY-HOME-TREE: ~/ab: >= 50001 files')
    assert 'tree acquire' in line
    assert 'clean --root <home> ab' in line
