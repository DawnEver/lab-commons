"""``lab_commons.dev.famtests.hookinstall`` -- driven on REAL git repositories with REAL hook files.

WHY REAL REPOSITORIES. The whole subject is the directory GIT WILL ACTUALLY CONSULT, and that is the
one thing a fixture cannot assert into being: ``core.hooksPath`` redirects it repository-wide and a
worktree resolves to the main checkout's shared directory rather than getting one of its own. So
every case here runs ``git init`` for real, writes real hook files into the directory git names, and
-- for the redirect and worktree arms -- moves that directory under the guard's feet and re-asks.

THE EXACT-SET PIN IS THE PROPERTY, AND IT IS DRIVEN IN BOTH DIRECTIONS. One repo's forked copy of
this guard asserts a SUPERSET of the stages it expects, which is the count-pin failure in set form:
it cannot say WHICH stage moved, so a stage silently added and never installed compares equal and a
stage deleted leaves the pin reading as a decision. :func:`test_a_stage_added_to_the_configuration_reds`
and :func:`test_a_stage_removed_from_the_configuration_reds` are the two halves that a superset
assertion passes and this one does not.

EVERY PLANTED CONTROL GOES BOTH WAYS. A repo with nothing installed sits beside the same repo with
pre-commit's own shim in place; a foreign hook sits beside a generated one; a remedy naming every
stage sits beside one naming a subset.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lab_commons.dev.famtests.hookinstall import (
    assert_a_foreign_hook_is_not_installed,
    assert_declared_hooks_are_installed,
    assert_hooks_dir_is_the_one_git_consults,
    assert_stages_are_declared,
    assert_the_guard_can_go_both_ways,
    assert_the_remedy_is_derived,
    remedy_stages,
)
from lab_commons.dev.hook_install import DEFAULT_CONFIG_NAME, generated_hook, hooks_dir

_GIT = shutil.which('git') or 'git'

#: A configuration declaring two stages, one of them EXPLICITLY -- the implicit-stage case is what
#: made a real hole invisible in one repo for as long as it existed, so it is carried here.
_CONFIG = """repos:
  - repo: https://example.invalid/hooks
    rev: v1.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: commitizen
        stages: [commit-msg]
"""

#: What the configuration above declares, read once so the fixtures and the arms cannot disagree.
_STAGES = frozenset({'commit-msg', 'pre-commit'})


def _repo(root: Path, *, config: str = _CONFIG) -> Path:
    """A REAL repository carrying a REAL configuration, with nothing installed yet."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run([_GIT, 'init', '-q', str(root)], check=True, capture_output=True, timeout=60)
    (root / DEFAULT_CONFIG_NAME).write_text(config, encoding='utf-8')
    return root


def _install(root: Path, *, stages: frozenset[str] = _STAGES) -> None:
    """Write pre-commit's own generated shim into the directory git names, for each stage."""
    directory = hooks_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    for stage in stages:
        (directory / stage).write_text(
            generated_hook(config_name=DEFAULT_CONFIG_NAME, hook_type=stage),
            encoding='utf-8',
        )


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """Declared and NOT installed -- the state measured in three repos on 2026-09-16."""
    return _repo(tmp_path / 'bare')


@pytest.fixture
def wired(tmp_path: Path) -> Path:
    """Declared AND installed. Without this arm every assertion below could be a constant."""
    root = _repo(tmp_path / 'wired')
    _install(root)
    return root


# --------------------------------------------------------------------------------------------
# the instrument control, and the exact-set pin


def test_the_declared_stages_are_pinned_as_a_set(wired: Path) -> None:
    """The green arm: the configuration declares exactly what the consumer says it declares."""
    assert_stages_are_declared(root=wired, config_name=DEFAULT_CONFIG_NAME, declared_stages=_STAGES)


def test_a_stage_added_to_the_configuration_reds(tmp_path: Path) -> None:
    """A SUPERSET ASSERTION PASSES THIS. A stage nobody installed must not compare equal."""
    root = _repo(tmp_path / 'grown', config=_CONFIG + '      - id: no-commit-to-branch\n        stages: [pre-push]\n')
    _install(root)
    with pytest.raises(AssertionError, match='pre-push'):
        assert_stages_are_declared(root=root, config_name=DEFAULT_CONFIG_NAME, declared_stages=_STAGES)


def test_a_stage_removed_from_the_configuration_reds(wired: Path) -> None:
    """The other side of the ratchet: a pin outliving its stage reads as a decision and is not one."""
    with pytest.raises(AssertionError, match='pre-push'):
        assert_stages_are_declared(
            root=wired,
            config_name=DEFAULT_CONFIG_NAME,
            declared_stages=frozenset({*_STAGES, 'pre-push'}),
        )


def test_a_repository_declaring_nothing_reds_rather_than_watching_nothing(tmp_path: Path) -> None:
    """THE FLOOR. No configuration means no stages means every other arm is trivially satisfied."""
    root = tmp_path / 'silent'
    root.mkdir()
    subprocess.run([_GIT, 'init', '-q', str(root)], check=True, capture_output=True, timeout=60)
    with pytest.raises(AssertionError, match='watching nothing'):
        assert_stages_are_declared(root=root, config_name=DEFAULT_CONFIG_NAME, declared_stages=_STAGES)


def test_an_unreadable_configuration_raises_rather_than_declaring_nothing(tmp_path: Path) -> None:
    """The real version of the arm all three forked copies spelled and none of them could red.

    Each carried ``assert all(stage.hook_ids ...)``, which cannot fail: a stage is in the report only
    because a hook id put it there. What CAN happen is a configuration the parser could not read, and
    that is refused one layer down rather than reported as an empty declaration.
    """
    single = _repo(tmp_path / 'single', config='repos:\n  - repo: local\n    hooks:\n      - id: only\n')
    _install(single, stages=frozenset({'pre-commit'}))
    assert_stages_are_declared(
        root=single,
        config_name=DEFAULT_CONFIG_NAME,
        declared_stages=frozenset({'pre-commit'}),
    )
    unread = _repo(tmp_path / 'unread', config='# a file with no repos: key at all\n')
    with pytest.raises(ValueError, match='repos'):
        assert_stages_are_declared(
            root=unread,
            config_name=DEFAULT_CONFIG_NAME,
            declared_stages=frozenset({'pre-commit'}),
        )


# --------------------------------------------------------------------------------------------
# the property


def test_an_installed_repository_passes(wired: Path) -> None:
    """Green means the commits through this checkout really are guarded."""
    assert_declared_hooks_are_installed(root=wired, config_name=DEFAULT_CONFIG_NAME)


def test_a_declared_but_uninstalled_repository_reds_and_names_the_derived_remedy(bare: Path) -> None:
    """A red must carry WHICH stages, and the argv that fixes them, built from the configuration."""
    with pytest.raises(AssertionError) as caught:
        assert_declared_hooks_are_installed(root=bare, config_name=DEFAULT_CONFIG_NAME)
    message = str(caught.value)
    assert 'commit-msg' in message, message
    assert 'pre-commit' in message, message
    assert '-m pre_commit install' in message, message
    assert 'deliberate' in message, 'a shared hooks directory must never be repaired silently'


def test_a_half_installed_repository_reds(tmp_path: Path) -> None:
    """The state a superset-style guard is worst at: one stage live, one not, and a green top line."""
    root = _repo(tmp_path / 'half')
    _install(root, stages=frozenset({'pre-commit'}))
    with pytest.raises(AssertionError, match='commit-msg'):
        assert_declared_hooks_are_installed(root=root, config_name=DEFAULT_CONFIG_NAME)


# --------------------------------------------------------------------------------------------
# the directory git will consult


def test_the_hooks_directory_matches_what_git_names(wired: Path) -> None:
    """Cross-checked against git's own answer, not against a path this file rebuilt."""
    assert_hooks_dir_is_the_one_git_consults(root=wired)


def test_a_redirected_hooks_path_is_followed(tmp_path: Path) -> None:
    """``core.hooksPath`` moves the directory repository-wide; a string-built guard never notices."""
    root = _repo(tmp_path / 'redirected')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    subprocess.run(
        [_GIT, '-C', str(root), 'config', 'core.hooksPath', str(elsewhere)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    assert hooks_dir(root).resolve() == elsewhere.resolve()
    assert_hooks_dir_is_the_one_git_consults(root=root)

    # THE OTHER DIRECTION, AND IT HAD TO BE PLANTED THROUGH GIT: `git init` always creates
    # `.git/hooks`, so the only way to reach "the directory git names is not there" is to redirect
    # `core.hooksPath` at something that does not exist -- which is also the real-world shape, a
    # config line outliving the directory it points at.
    missing = _repo(tmp_path / 'gone')
    subprocess.run(
        [_GIT, '-C', str(missing), 'config', 'core.hooksPath', str(tmp_path / 'nowhere')],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with pytest.raises(AssertionError, match='not a directory'):
        assert_hooks_dir_is_the_one_git_consults(root=missing)


# --------------------------------------------------------------------------------------------
# the planted controls, as callables a consumer runs on its own tree


def test_the_both_ways_control_passes_on_a_real_configuration(wired: Path, tmp_path: Path) -> None:
    """It plants absent-then-installed in a fresh repo carrying the CONSUMER'S OWN configuration."""
    assert_the_guard_can_go_both_ways(
        root=wired,
        config_name=DEFAULT_CONFIG_NAME,
        scratch=tmp_path / 'both',
    )


def test_the_both_ways_control_refuses_a_configuration_it_cannot_read(tmp_path: Path) -> None:
    """A control built on a configuration that parses to nothing plants nothing and proves nothing."""
    root = _repo(tmp_path / 'unreadable', config='# just a comment\n')
    with pytest.raises((AssertionError, ValueError)):
        assert_the_guard_can_go_both_ways(
            root=root,
            config_name=DEFAULT_CONFIG_NAME,
            scratch=tmp_path / 'both2',
        )


def test_a_foreign_hook_is_not_counted_as_installed(wired: Path, tmp_path: Path) -> None:
    """Re-installing over a hand-written hook DELETES it, so the two findings must stay apart."""
    assert_a_foreign_hook_is_not_installed(
        root=wired,
        config_name=DEFAULT_CONFIG_NAME,
        scratch=tmp_path / 'foreign',
    )


def test_the_foreign_control_plants_every_declared_stage(tmp_path: Path) -> None:
    """MEASURED 2026-09-17: a fixture that planted ONE stage reported about all of them.

    The unplanted stage answered ``declared-but-absent`` -- a TRUE finding about the fixture and a
    FALSE one about the status under test. Planting every declared stage is what keeps this arm
    reading about FOREIGNNESS, so a configuration with two stages must exercise both.
    """
    root = _repo(tmp_path / 'twostage')
    _install(root)
    assert_a_foreign_hook_is_not_installed(
        root=root,
        config_name=DEFAULT_CONFIG_NAME,
        scratch=tmp_path / 'foreign2',
    )
    # `git init` populates the directory with its own `*.sample` files, which are not hooks git
    # runs; what this arm is about is that the control wrote a file for EVERY declared stage.
    planted = hooks_dir(tmp_path / 'foreign2' / 'checkout')
    written = {path.name for path in planted.iterdir() if path.is_file() and path.suffix != '.sample'}
    assert written == set(_STAGES), written


# --------------------------------------------------------------------------------------------
# the remedy


def test_the_remedy_names_every_declared_stage(wired: Path) -> None:
    """A remedy naming a SUBSET leaves one stage quietly uninstalled -- measured on a fresh box."""
    assert_the_remedy_is_derived(root=wired, config_name=DEFAULT_CONFIG_NAME)


def test_the_remedy_check_would_see_a_restated_stage_list(bare: Path) -> None:
    """THE CONTROL FOR THE CONTROL: the comparison is against the CONFIGURATION, not a constant."""
    named = remedy_stages(root=bare, config_name=DEFAULT_CONFIG_NAME)
    assert named == _STAGES, named
    assert named != frozenset({'pre-commit'}), 'a one-stage answer here would pass a subset remedy'


# --------------------------------------------------------------------------------------------
# the seam itself


def test_every_repo_shaped_fact_is_required() -> None:
    """NO DEFAULTS. A guessed config name judges the wrong file; a guessed stage set judges nothing."""
    with pytest.raises(TypeError, match='declared_stages'):
        assert_stages_are_declared(root=Path(), config_name=DEFAULT_CONFIG_NAME)
    with pytest.raises(TypeError, match='config_name'):
        assert_declared_hooks_are_installed(root=Path())
