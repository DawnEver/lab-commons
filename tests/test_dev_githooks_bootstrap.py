"""The consumer-side bootstrap, over PLANTED checkouts and against a REAL ``pre-commit``.

TWO TIERS, AND THE SECOND IS THE ONE THAT MATTERS. The resolution tests below plant real git
checkouts and real venvs and drive :mod:`lab_commons.dev.githooks.bootstrap` over them, which pins
what it CHOOSES. They say nothing whatsoever about the claim the module was written for -- that a
``.pre-commit-config.yaml`` can name the entry LITERALLY with no interpreter in front of it -- because
that is a claim about what ``pre-commit`` puts on ``PATH``, and only ``pre-commit`` can answer it.
:func:`test_the_entry_is_nameable_with_no_interpreter_in_front` builds a throwaway git repo and runs a
real ``pre-commit`` against it, which is the same method the measurement that motivated the module
used, and it is deliberately the expensive test in this file.

NOTHING HERE SKIPS. The integration test provisions what it needs -- its own interpreter, its own
``pre-commit`` -- rather than asking the box whether it happens to have one, because a precondition
that is absent on a box reads as a green suite on that box, and this repo's skip registry is empty by
policy. Where a provision genuinely cannot be made the test FAILS and says which one, for the reason
:class:`~lab_commons.dev.githooks.NoBash` gives: a box that cannot run the hook mechanism runs no
hooks at all, and that is a finding rather than a reason to report success.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from lab_commons.dev.githooks import bootstrap

#: The distribution spec a consumer's hook declares. Pointed at THIS checkout rather than at the
#: published git URL so the test judges the code in the tree, which is the only copy a commit can be
#: answerable for; the shipped spelling is the git URL and lives in the module's docstring.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: A module planted into ONE venv's site-packages so the two can be told apart by the caller's own
#: question. Named after what it is for, because a probe module that reads like a real dependency is
#: how a planted control starts being trusted as a measurement.
PROBE_MODULE = 'which_checkout_am_i'

#: What the probe module DOES when run through ``-m``. It prints, because the integration test must
#: read which interpreter was resolved: an exit code of 0 is also what a bootstrap that did nothing
#: would produce, and telling those apart is the whole subject of this file.
_PROBE_SOURCE = """import sys
print('RESOLVED-INTERPRETER', sys.executable)
"""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def _git(cwd: Path, *args: str) -> None:
    _run(['git', *args], cwd)


def _new_checkout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, 'init', '-q', '.')
    _git(root, 'config', 'user.email', 'planted@example.invalid')
    _git(root, 'config', 'user.name', 'planted')
    (root / 'README.md').write_text('planted\n', encoding='utf-8')
    _git(root, 'add', 'README.md')
    _git(root, 'commit', '-qm', 'init')
    return root


def _new_venv(root: Path, *, with_pip: bool = True) -> Path:
    """A REAL venv under *root*, because every probe in the module asks a real question of it."""
    venv.EnvBuilder(with_pip=with_pip).create(root / '.venv')
    return root / '.venv'


def _site_packages(venv_dir: Path) -> Path:
    for candidate in (*venv_dir.glob('lib/python*/site-packages'), venv_dir / 'Lib' / 'site-packages'):
        if candidate.is_dir():
            return candidate
    msg = f'{venv_dir}: a venv was created with no site-packages, so nothing here can be planted'
    raise AssertionError(msg)


@pytest.fixture(scope='module')
def main_checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A MAIN checkout with a populated venv, and one module that only IT can import."""
    root = _new_checkout(tmp_path_factory.mktemp('main'))
    venv_dir = _new_venv(root)
    (_site_packages(venv_dir) / f'{PROBE_MODULE}.py').write_text(_PROBE_SOURCE, encoding='utf-8')
    return root


@pytest.fixture
def lane(main_checkout: Path, tmp_path: Path) -> Path:
    """A WORKTREE of *main_checkout* with no venv of its own -- the borrow-MAIN arm's subject."""
    lane_root = tmp_path / 'lane'
    _git(main_checkout, 'worktree', 'add', '-q', '-b', f'lane-{tmp_path.name}', str(lane_root))
    return lane_root


def test_a_venv_less_worktree_borrows_the_main_checkouts(main_checkout: Path, lane: Path) -> None:
    """ARM ONE: no venv here, so MAIN's is resolved -- through git, never through a spelled path."""
    roots = bootstrap.repo_roots(lane)
    assert roots[0] == lane.resolve(), f'this checkout must come first, got {roots}'
    assert main_checkout.resolve() in roots, f'MAIN must be reachable from a worktree, got {roots}'

    resolved = bootstrap.find_interpreter(roots, PROBE_MODULE)
    assert resolved.is_relative_to(main_checkout.resolve()), f"expected MAIN's interpreter, got {resolved}"


def test_a_worktree_with_its_own_venv_wins(main_checkout: Path, lane: Path) -> None:
    """ARM TWO, THE OTHER DIRECTION PLANTED. The same lane, once it HAS one, uses its own.

    A lane's own environment is the isolated case (user directive, restated 2026-08-10): a dependency
    change belongs to the lane rather than to everyone. Planted as a CHANGE to the arm above rather
    than as a separate fixture, so the two arms differ in exactly one fact.
    """
    lane_venv = _new_venv(lane)
    (_site_packages(lane_venv) / f'{PROBE_MODULE}.py').write_text(_PROBE_SOURCE, encoding='utf-8')

    resolved = bootstrap.find_interpreter(bootstrap.repo_roots(lane), PROBE_MODULE)
    assert resolved.is_relative_to(lane.resolve()), f"expected the lane's own interpreter, got {resolved}"
    assert not resolved.is_relative_to(main_checkout.resolve()), f'the lane venv must win, got {resolved}'


def test_a_lane_venv_that_cannot_provide_the_module_yields_to_main(main_checkout: Path, lane: Path) -> None:
    """The 2026-09-14 lesson, planted: a REAL lane venv WITHOUT the asked-for module must not win.

    It has distributions, so the distribution probe says yes; it cannot import what this call needs,
    so it is not a candidate FOR THIS CALL. Getting this wrong produced ``No module named ruff`` on
    every commit in a lane, with a populated venv one fallback away and the message naming ruff.
    """
    _new_venv(lane)

    resolved = bootstrap.find_interpreter(bootstrap.repo_roots(lane), PROBE_MODULE)
    assert resolved.is_relative_to(main_checkout.resolve()), f'expected MAIN to be the fallback, got {resolved}'


def test_a_venv_with_no_distributions_is_not_an_environment(main_checkout: Path, lane: Path) -> None:
    """The 2026-08-15 lesson, planted: a STUB ``.venv`` holding an executable and nothing else loses.

    An interpreter that exists is not an environment that works. A stub is what an interrupted
    creation leaves, and an executability probe says yes to it.
    """
    stub = lane / '.venv' / ('Scripts' if os.name == 'nt' else 'bin')
    stub.mkdir(parents=True)
    executable = stub / ('python.exe' if os.name == 'nt' else 'python')
    executable.write_bytes(Path(sys.executable).read_bytes())
    executable.chmod(0o755)

    resolved = bootstrap.find_interpreter(bootstrap.repo_roots(lane), PROBE_MODULE)
    assert resolved.is_relative_to(main_checkout.resolve()), f'a stub must not win, got {resolved}'


def test_no_venv_anywhere_names_the_missing_ENVIRONMENT(tmp_path: Path) -> None:
    """FAIL LOUDLY, and with the remedy for the case that actually happened.

    The two failures have DIFFERENT remedies and the wrong one sends the reader to the wrong place,
    so the message is asserted rather than merely the exception.
    """
    root = _new_checkout(tmp_path / 'bare')

    with pytest.raises(bootstrap.NoInterpreter, match=r'no populated \.venv'):
        bootstrap.find_interpreter(bootstrap.repo_roots(root), PROBE_MODULE)


def test_a_venv_without_the_tool_names_the_missing_TOOL(tmp_path: Path) -> None:
    """The other branch: a venv EXISTS, so this is a missing tool and installing a venv is not it."""
    root = _new_checkout(tmp_path / 'toolless')
    _new_venv(root)

    with pytest.raises(bootstrap.NoInterpreter, match='missing TOOL'):
        bootstrap.find_interpreter(bootstrap.repo_roots(root), 'no_such_module_anywhere')


def test_main_returns_one_and_says_so_when_it_cannot_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE ONE OUTCOME THIS MODULE EXISTS TO MAKE IMPOSSIBLE is exit 0 having done nothing.

    A bootstrap that returns quietly is read by pre-commit as a hook that PASSED -- the defect the
    ``KINDS`` registry was built to refuse, arriving by a new route.
    """
    monkeypatch.chdir(_new_checkout(tmp_path / 'silent'))

    code = bootstrap.main(['ruff', 'check'])

    assert code == 1, 'a failure to bootstrap must never be reported as a pass'
    assert 'lab-with-venv' in capsys.readouterr().err, 'the refusal must name itself on stderr'


@pytest.mark.parametrize(
    ('argv', 'expected_tail'),
    [
        (['ruff', 'check', '--force-exclude'], ['-m', 'ruff', 'check', '--force-exclude']),
        (['lab_commons.dev.githooks', 'bump-version'], ['-m', 'lab_commons.dev.githooks', 'bump-version']),
        (['scripts/gate/prepush_gate.py', '--x'], ['scripts/gate/prepush_gate.py', '--x']),
    ],
)
def test_command_for_dispatches_a_script_path_away_from_dash_m(argv: list[str], expected_tail: list[str]) -> None:
    """A SCRIPT PATH is not a module name. ``-m`` on one fails with ``No module named python``.

    That is not hypothetical: it is what silently disabled a consumer's entire pre-push smoke for the
    span between two commits, while two unrelated repairs were made to the selector it never reached.
    """
    assert bootstrap.command_for(Path('P'), argv) == ['P', *expected_tail]


def test_pyright_also_pins_the_environment_it_analyses_against() -> None:
    """Resolving the INTERPRETER is only half of it, and the other half bites only from a lane.

    pyright reads the environment it ANALYSES from the project root it runs in, so a venv-less
    worktree analyses against the system interpreter and every third-party import goes unresolved --
    41 spurious errors, measured 2026-07-28. The SOURCE analysed stays the caller's.
    """
    assert bootstrap.command_for(Path('P'), ['pyright', 'src']) == ['P', '-m', 'pyright', '--pythonpath', 'P', 'src']


def test_command_for_refuses_an_empty_command() -> None:
    """Nothing to run is a refusal, not a zero-exit no-op, for the same reason as everything above."""
    with pytest.raises(bootstrap.NoInterpreter, match='nothing to run'):
        bootstrap.command_for(Path('P'), [])


@pytest.mark.parametrize(
    ('planted', 'survives'),
    [
        ({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'k', 'GIT_CONFIG_VALUE_0': 'v'}, True),
        ({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'k'}, False),
    ],
)
def test_an_incomplete_git_config_set_is_dropped_whole(planted: dict[str, str], *, survives: bool) -> None:
    """BOTH DIRECTIONS. A COMPLETE set is untouched; an incomplete one is dropped entirely.

    git rejects an incomplete set wholesale with ``missing config value`` and exit 128 before running
    anything, so there is nothing in it to preserve -- but a repair that also dropped a VALID set
    would be silently discarding a caller's configuration, which is why the first row exists.
    """
    out = bootstrap.sanitized_env({'PATH': 'kept', **planted})

    assert out['PATH'] == 'kept', 'the rest of the environment is never touched'
    assert ('GIT_CONFIG_COUNT' in out) is survives, f'GIT_CONFIG_COUNT handling wrong for {planted}'


# --------------------------------------------------------------------- the one that needs pre-commit

#: The hook body. It prints the resolved interpreter so the assertion reads what the CONSUMER's venv
#: is, rather than trusting an exit code that a bootstrap doing nothing would also produce.
_CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: bootstrap
        name: bootstrap
        entry: lab-with-venv
        args: ['{module}']
        language: python
        additional_dependencies: ['{spec}']
        always_run: true
        pass_filenames: false
        verbose: true
"""


@pytest.fixture(scope='module')
def precommit(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A REAL ``pre-commit`` executable, PROVISIONED rather than looked for.

    Looking for one on ``PATH`` is the shape this whole module is about: a box without it would
    report a green suite for the claim that matters most. So the test builds an isolated environment
    of its own -- outside every shared venv, which is why it may install at all -- and fails naming
    the provision if it cannot.
    """
    root = tmp_path_factory.mktemp('precommit-env')
    venv.EnvBuilder(with_pip=True).create(root)
    python = next(p for p in (root / 'Scripts' / 'python.exe', root / 'bin' / 'python') if p.exists())
    _run([str(python), '-m', 'pip', 'install', '--quiet', 'pre-commit'], root)
    found = next(
        (p for p in (root / 'Scripts' / 'pre-commit.exe', root / 'bin' / 'pre-commit') if p.exists()),
        None,
    )
    if found is None:
        msg = f'pre-commit installed into {root} and left no executable, so the claim cannot be measured'
        raise AssertionError(msg)
    return found


def test_the_entry_is_nameable_with_no_interpreter_in_front(
    precommit: Path,
    main_checkout: Path,
    lane: Path,
) -> None:
    """THE CLAIM. ``entry: lab-with-venv`` runs, from a hook, with nothing in front of it.

    Three things are measured at once and each was a way this could have failed:

    * pre-commit resolves the console script AT ALL from a ``repo: local`` hook, which is only true
      because ``language: python`` prepends its own managed environment to ``PATH`` -- the same entry
      under ``language: system`` cannot be written, since no venv is on ``PATH`` there at all;
    * the bootstrap then finds the CONSUMER's interpreter rather than the one that launched it, which
      is what keeps the managed environment a launcher instead of a second install of this package;
    * and it finds it from a worktree with NO venv of its own, borrowing MAIN's -- the arm a
      documented literal path into ``site-packages`` would have lost.
    """
    config = _CONFIG.format(module=PROBE_MODULE, spec=REPO_ROOT.as_posix())
    (lane / '.pre-commit-config.yaml').write_text(config, encoding='utf-8')
    _git(lane, 'add', '.pre-commit-config.yaml')
    _git(lane, 'commit', '-qm', 'config', '--no-verify')

    done = subprocess.run(
        [str(precommit), 'run', '--all-files', '--verbose'],
        cwd=lane,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, 'PRE_COMMIT_HOME': str(lane.parent / 'pc-home')},
    )
    output = done.stdout + done.stderr

    assert done.returncode == 0, f'the hook did not pass:\n{output}'
    assert 'RESOLVED-INTERPRETER' in output, f'the console script never reached the probe:\n{output}'
    resolved = next(line for line in output.splitlines() if 'RESOLVED-INTERPRETER' in line)
    assert main_checkout.resolve().as_posix() in Path(resolved.split(maxsplit=1)[1].strip()).as_posix(), (
        f"the hook ran an interpreter that is not the consumer's: {resolved}"
    )
