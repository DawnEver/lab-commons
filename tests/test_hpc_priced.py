"""A REPO DECLARES ITS PRICED FLOORS: ``[tool.lab_commons.hpc] priced`` names a TOML of known costs.

Run 4fb97faa904f: a heavy id ran out of memory in round 0 on every host until that host had learned it. The
declaration is read at the commit being run and merged into the plan's :class:`Measured` as FLOORS -- a learned
value above it wins -- and never into the learned table.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from lab_commons.compute.measured import Measured
from lab_commons.hpc.builds import builds_at, read_priced
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.declared import InvalidDeclaration

_GIT: Final = shutil.which('git') or 'git'
HEAVY: Final = 'tests/b.py::t3[sdm]'
LIGHT: Final = 'tests/b.py::t4'


def test_a_declared_floor_sizes_round_0() -> None:
    """PLANTED: nothing learned, the declared 9 GB id asks 9 GB times the safety; its wall is the declared one."""
    bare = Measured()
    priced = bare.floored({HEAVY: {'mem_gb': 9.0, 'wall_s': 1200.0}})
    assert bare.item_gb([HEAVY], Cost(), Policy()) == pytest.approx(Cost().mem_gb)
    assert priced.item_gb([HEAVY], Cost(), Policy()) == pytest.approx(9.0 * Policy().safety)
    assert priced.floors == {HEAVY: 1200.0}
    assert bare.record() == Measured().record(), 'the learned facts are never written by a declaration'


@pytest.mark.parametrize('safety', [1.0, 1.3, 2.0])
def test_a_declared_floor_is_multiplied_by_exactly_the_one_safety_factor(safety: float) -> None:
    """ONE MARGIN, ONE PLACE (user ruling 2026-10-31): a declared raw peak asks peak x ``Policy.safety``, nothing more.

    motronics declared peak x 1.5 and the plan multiplied its own 1.5 on top (~2.25x). The declaration is the
    measurement; the margin is the policy's. PLANTED: at ``safety=1.0`` the ask IS the declared peak, so a
    second factor anywhere would show.
    """
    policy, cost = Policy(safety=safety), Cost(mem_gb=0.25)
    for key, ids in ((HEAVY, [HEAVY]), ('tests/b.py', [LIGHT])):
        priced = Measured().floored({key: {'mem_gb': 6.0}})
        assert priced.item_gb(ids, cost, policy) == pytest.approx(6.0 * safety), key


def test_a_higher_learned_value_wins_over_the_floor() -> None:
    learned = Measured(id_peaks_mb={HEAVY: 20 * 1024.0}, floors={HEAVY: 5000.0}, peaks_mb={'tests/b.py': 30000.0})
    priced = learned.floored({HEAVY: {'mem_gb': 9.0, 'wall_s': 1200.0}, 'tests/b.py': {'mem_gb': 1.0}})
    assert priced.record() == learned.record(), 'a floor never lowers what a host learned'


def test_an_undeclared_id_is_unchanged() -> None:
    learned = Measured(durations={LIGHT: 3.0}, peaks_mb={'tests/a.py': 700.0})
    priced = learned.floored({HEAVY: {'mem_gb': 9.0}})
    assert priced.durations == {LIGHT: 3.0}
    assert priced.item_gb(['tests/a.py::x'], Cost(), Policy()) == learned.item_gb(['tests/a.py::x'], Cost(), Policy())
    assert HEAVY not in priced.floors, 'a declaration without a wall floors no wall'


def test_a_file_key_floors_the_files_peak_and_its_wall() -> None:
    priced = Measured().floored({'tests/b.py': {'mem_gb': 4.0, 'wall_s': 900.0}})
    assert priced.peaks_mb == {'tests/b.py': 4096.0}
    assert priced.estimate([[LIGHT]], Cost(seconds=60))[0] == [900.0], 'a declared file is not unmeasured'


GOOD: Final = f'"{HEAVY}" = {{ mem_gb = 9.6, wall_s = 750 }}\n"tests/b.py" = {{ mem_gb = 3 }}\n'


def test_a_wellformed_file_reads_as_floors() -> None:
    assert read_priced(GOOD, 'priced.toml') == {HEAVY: {'mem_gb': 9.6, 'wall_s': 750.0}, 'tests/b.py': {'mem_gb': 3.0}}


@pytest.mark.parametrize(
    ('text', 'named'),
    [
        (f'"{HEAVY}" = {{ mem_gb = 9, rss = 3 }}\n', 'rss'),
        (f'"{HEAVY}" = {{ mem_gb = -1 }}\n', 'mem_gb'),
        (f'"{HEAVY}" = {{ wall_s = true }}\n', 'wall_s'),
        (f'"{HEAVY}" = {{}}\n', HEAVY),
        (f'"{HEAVY}" = 9\n', HEAVY),
        ('"cases/x" = { mem_gb = 1 }\n', 'cases/x'),
        ('"tests/b.py" = { mem_gb = 1 \n', 'priced.toml'),
    ],
)
def test_a_malformed_file_is_refused_by_name(text: str, named: str) -> None:
    """PLANTED: each defect is refused, naming what is wrong; the GOOD file above is the control."""
    with pytest.raises(InvalidDeclaration, match=re.escape(named)):
        read_priced(text, 'priced.toml')


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).write_text(text, encoding='utf-8')
    _git(repo, 'add', '.')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'c')
    return _git(repo, 'rev-parse', 'HEAD')


PYPROJECT: Final = '[project]\nname = "x"\n[tool.lab_commons.hpc]\ninstall = "uv sync"\n'


def test_the_floors_are_read_at_the_commit_being_run(tmp_path: Path) -> None:
    """PLANTED: the file moves after the commit; the commit's own floors are what its run plans with."""
    _git(tmp_path, 'init', '-q')
    sha = _commit(tmp_path, {'pyproject.toml': PYPROJECT + 'priced = "priced.toml"\n', 'priced.toml': GOOD})
    (tmp_path / 'priced.toml').write_text('', encoding='utf-8')
    builds = builds_at(tmp_path, sha, python='3.13')
    assert builds.priced == 'priced.toml'
    assert builds.priced_floors == read_priced(GOOD, 'priced.toml')
    undeclared = _commit(tmp_path, {'pyproject.toml': PYPROJECT})
    assert builds_at(tmp_path, undeclared, python='3.13').priced_floors == {}
    absent = _commit(tmp_path, {'pyproject.toml': PYPROJECT + 'priced = "gone.toml"\n'})
    with pytest.raises(InvalidDeclaration, match=re.escape('gone.toml')):
        builds_at(tmp_path, absent, python='3.13')
