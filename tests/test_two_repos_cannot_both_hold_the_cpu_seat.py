"""ONE-BOX-ONE-LOCK, proved the only way it can be: TWO REAL CHECKOUTS, TWO REAL INTERPRETERS.

THE FAILURE MODE THIS EXISTS TO CATCH, and every part of it is SILENT. The adoption lands, every
repo calls ``CpuSeat``, and the seats never meet -- ``$LAB_COMMONS_RESOURCE_DIR`` set in one repo's
environment and not the other, two ``lab_commons`` versions in two virtualenvs, or a ``Scope``
regression that makes ``BOX_SEATS`` pool-scoped again. Both runs start, both succeed, both print a
clean verdict, and the only symptom is a box that swaps. That is strictly WORSE than taking no lock
at all, because a repo that refuses at least tells somebody.

A SINGLE-PROCESS TEST CANNOT SEE ANY OF IT. Two ``CpuSeat`` objects in one interpreter agree about
the record root because they read the same ``os.environ``; the exclusion has to travel through the
FILESYSTEM or this test is asserting that a dataclass equals itself. So: a holder in a subprocess
with its own cwd, a HEAVY run's real seat call (``hold_run_seats``, :func:`_run_contender` says why not
``verify``) in a differently-named checkout, and the verdict read off the second one's exit code and stdout.

THE THREE FLOORS, without which a green here is vacuous:

1. **The pool-name floor.** The holder names a pool of its OWN -- never ``CPU_POOL`` -- and demands
   only ``BOX_SEATS``. If a future edit makes that dimension pool-scoped, the two never meet, the
   contender is ADMITTED, and :func:`test_a_holder_under_its_own_pool_name_still_excludes_a_second_checkout`
   reds. Without this the suite would pass against a mechanism that only excludes repos which
   happened to agree on a string.
2. **The positive floor.** With the two checkouts pointed at DIFFERENT record roots, the contender must
   be ADMITTED. A test that only ever sees a refusal cannot tell exclusion from a broken
   subprocess, a missing import or a checkout that never ran.
3. **The named-holder floor.** The refusal must contain the holder's ``what``. "Inconclusive" with
   no name sends its reader to the process table to guess.

The pid-reuse floor the same rule needs is in ``tests/test_liveness.py``, where the two incidents
that decide the liveness oracle live together.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lab_commons.dev.producer import PRODUCING_VAR

#: What the holder calls itself. Asserted verbatim in the refusal, so the refusal is checked to NAME
#: rather than merely to refuse.
HOLDER_WHAT = 'the alpha checkout gate'

#: The holder's OWN pool name. Deliberately not ``CPU_POOL``: see floor 1 in the module docstring.
HOLDER_POOL = 'alpha-lane'

#: Exit codes the refusal is judged by -- the same three ``verify`` publishes.
PASS, FAIL, INCONCLUSIVE = 0, 1, 3

#: How long to wait for the holder's sentinel before calling the planted subprocess broken. A
#: ceiling on the WAIT, not a sleep: the loop returns the moment the file appears.
SENTINEL_WAIT_S = 60.0

#: The holder: take the box under a pool of its own, announce it, and hold until told to stop.
_HOLDER = """
import pathlib, sys, time
from lab_commons.resources import BOX_SEATS, Broker, Capacity, CapacityRegistry

root, sentinel, stop, what, pool = sys.argv[1:6]
registry = CapacityRegistry([(pool, Capacity.structural(BOX_SEATS.name, 1))])
broker = Broker(registry, resource_dir=pathlib.Path(root))
with broker.admit(pool, {BOX_SEATS.name: 1}, what=what):
    pathlib.Path(sentinel).write_text('held', encoding='utf-8')
    while not pathlib.Path(stop).exists():
        time.sleep(0.05)
"""

#: A minimal but REAL checkout, so the contender runs in a repository of its own.
_PYPROJECT = """[project]
name = '{name}'
version = '0.0.1'

[tool.pytest.ini_options]
testpaths = ['tests']
"""

_MODULE = """\"\"\"A module with a public surface, so ruff has something real to read.\"\"\"

__all__ = ["answer"]


def answer() -> int:
    \"\"\"The one fact this checkout knows.\"\"\"
    return 42
"""

_TEST = """from {package} import answer


def test_the_checkout_runs_its_own_suite() -> None:
    assert answer() == 42
"""


def _checkout(parent: Path, name: str, package: str) -> Path:
    """A differently-NAMED git checkout with its own package name, ruff-clean and pytest-green.

    The directory name and the distribution name both differ between the two, so nothing about the
    exclusion can come from the two runs sharing a path, a package or an import.
    """
    root = parent / name
    (root / 'src' / package).mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'pyproject.toml').write_text(_PYPROJECT.format(name=name), encoding='utf-8')
    (root / 'src' / package / '__init__.py').write_text(_MODULE, encoding='utf-8')
    (root / 'tests' / 'test_smoke.py').write_text(_TEST.format(package=package), encoding='utf-8')
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True, capture_output=True)  # noqa: S607 -- git through PATH
    return root


#: The contender: a HEAVY run's seats taken the way every adopter takes them, ``hold_run_seats`` with no
#: memory declared, then the verdict ``verify`` publishes -- PASS when admitted, INCONCLUSIVE naming the
#: holder when refused.
_CONTENDER = """
import contextlib, sys
from lab_commons.dev.boxwait import hold_run_seats, holders_line
from lab_commons.resources import Exhausted

try:
    with contextlib.ExitStack() as stack:
        hold_run_seats(stack, 'the beta checkout gate', wait_s=0.0, poll_s=0.05)
        print('admitted')
except Exhausted as refused:
    print('refused -- held by: ' + holders_line(refused))
    sys.exit(3)
"""


def _run_contender(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """A HEAVY run's seats taken in *root* by a second interpreter, with a ZERO wait so the test bounds itself.

    NOT ``python -m lab_commons.dev.verify`` any more, and the reason is two user rulings meeting: a run
    ``forge serve`` spawned takes no box seat, and a targeted (LIGHT) selection takes none either (user
    ruling 2026-10-13: light runs never queue) -- while an interactive full suite is refused as a verdict
    before it reaches a seat. Every verify an interactive caller can start is light, so verify can no
    longer stand for "heavy excludes heavy"; the seat call every heavy adopter makes can. The producer
    marker is stripped even when this suite itself runs inside one, or the contender would take no seat.
    """
    inherited = {k: v for k, v in os.environ.items() if k != PRODUCING_VAR}
    return subprocess.run(
        [sys.executable, '-c', _CONTENDER],
        cwd=root,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=600,
        check=False,
        env=inherited | environment,
    )


def _await_sentinel(sentinel: Path, holder: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + SENTINEL_WAIT_S
    while not sentinel.exists():
        assert holder.poll() is None, f'the planted holder exited before taking the box (rc={holder.returncode})'
        assert time.monotonic() < deadline, f'the planted holder never took the box within {SENTINEL_WAIT_S}s'
        time.sleep(0.05)


@pytest.fixture
def checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """Two checkouts that share nothing but the box."""
    return _checkout(tmp_path, 'alpha-repo', 'alpha_pkg'), _checkout(tmp_path, 'beta-repo', 'beta_pkg')


def test_a_holder_under_its_own_pool_name_still_excludes_a_second_checkout(
    tmp_path: Path, checkouts: tuple[Path, Path]
) -> None:
    """THE CHECK, with floors 1 and 3 inside it: B is refused INCONCLUSIVE and the refusal NAMES A.

    The holder never mentions ``CPU_POOL``. What excludes is the box-SCOPED dimension, whose record
    files are one set per box rather than one set per pool -- so a repo that names its own pool is
    contended with rather than invisible.
    """
    alpha, beta = checkouts
    records = tmp_path / 'box-records'
    records.mkdir()
    sentinel, stop = tmp_path / 'held', tmp_path / 'stop'
    holder = subprocess.Popen(
        [sys.executable, '-c', _HOLDER, str(records), str(sentinel), str(stop), HOLDER_WHAT, HOLDER_POOL],
        cwd=alpha,
    )
    try:
        _await_sentinel(sentinel, holder)
        refused = _run_contender(beta, {'LAB_COMMONS_RESOURCE_DIR': str(records)})
    finally:
        stop.write_text('go', encoding='utf-8')
        holder.wait(timeout=60)

    output = refused.stdout + refused.stderr
    assert refused.returncode == INCONCLUSIVE, (
        f'a second checkout ran while the box was held -- exit {refused.returncode}, which is the '
        f'silent over-subscription this file exists to catch.\n{output}'
    )
    assert HOLDER_WHAT in output, (
        f'the refusal must NAME the holder; a reader has to choose between waiting and stopping it, '
        f'and only the name separates those.\n{output}'
    )


def test_two_record_roots_admit_both_checkouts(tmp_path: Path, checkouts: tuple[Path, Path]) -> None:
    """FLOOR 2, THE POSITIVE CONTROL: pointed at different roots, B must be ADMITTED and PASS.

    Without it the refusal above proves only that the subprocess failed -- a missing import, a
    broken checkout and a real exclusion all exit non-zero, and this is what tells them apart. It
    also pins the ``$LAB_COMMONS_RESOURCE_DIR`` half of the failure mode the module docstring names:
    two roots IS the silent non-meeting, reproduced deliberately so its signature is on record.
    """
    alpha, beta = checkouts
    alpha_records, beta_records = tmp_path / 'alpha-records', tmp_path / 'beta-records'
    alpha_records.mkdir()
    beta_records.mkdir()
    sentinel, stop = tmp_path / 'held', tmp_path / 'stop'
    holder = subprocess.Popen(
        [sys.executable, '-c', _HOLDER, str(alpha_records), str(sentinel), str(stop), HOLDER_WHAT, HOLDER_POOL],
        cwd=alpha,
    )
    try:
        _await_sentinel(sentinel, holder)
        admitted = _run_contender(beta, {'LAB_COMMONS_RESOURCE_DIR': str(beta_records)})
    finally:
        stop.write_text('go', encoding='utf-8')
        holder.wait(timeout=60)

    output = admitted.stdout + admitted.stderr
    assert admitted.returncode == PASS, (
        f'the second checkout must be ADMITTED when nothing excludes it -- exit '
        f'{admitted.returncode}. A refusal-only test cannot tell exclusion from a broken '
        f'subprocess.\n{output}'
    )
    assert 'admitted' in output, f'the positive control must reach the held block\n{output}'
