r"""THE KIT RUNS ITS OWN HOOK MACHINERY: `.pre-commit-config.yaml` is rendered here AND installed here.

WHAT WAS WRONG UNTIL 2026-09-17. This repo ships `famconfig` (the base-plus-delta renderer both labs
adopted), `hook_install` (is a declared hook actually in the directory git consults), `hooks`,
`hook_adoption` and the `lab-with-venv` bootstrap -- and had no `.pre-commit-config.yaml` and zero
installed git hooks. Every commit here went through no check of any kind, in the one repo whose own
tests assert that a configuration nobody installed is a declaration that lies.

THE TWO HALVES ARE SEPARATE ARMS ON PURPOSE, because they fail apart. A rendered config with no shim
in the hooks directory is EXACTLY the defect the family measured in both labs the same day:
`commitizen` was declared at `commit-msg` in each and neither had ever installed the shim, so every
commit message in both trees went unchecked while both read as guarded. The config half cannot see
that and never could; only :func:`lab_commons.dev.hook_install.hook_installation`, which reads the
hooks directory git ACTUALLY consults, can.

NOTHING HERE RE-IMPLEMENTS THE KIT. Every assertion below is either one of the shared bodies in
:mod:`lab_commons.dev.famtests.configrender` -- the same ones both labs parametrize -- or a call into
:mod:`lab_commons.dev.hook_install`. This module supplies only what is a fact about THIS repo: the
root, the name, the delta table, the command that re-renders, and which stages this checkout installs.

THE COMPLETENESS ARM IS DELIBERATELY ABSENT and saying so is the point. ``assert_every_base_is_
accounted_for`` demands a Delta for all three kit bases; this repo has adopted ONE. `.gitignore` and
`Makefile` are open SPLITS rows in the config census with their own evidence, so calling that arm
here would make this suite green on a completeness that has not happened, and leaving it out silently
would be worse. The last test in this module is what holds the absence; it reds on adoption.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _famconfig_delta import DELTAS, INSTALLED_STAGES, PRECOMMIT, PRECOMMIT_DELTA, PRECOMMIT_HOOK_FLOOR, REPO

from lab_commons.dev.famconfig import HOOK_ID_CORE, RENDERED, artefact_base
from lab_commons.dev.famtests.configrender import (
    assert_artefact_is_rendered,
    assert_declared_ids_survive,
    assert_delta_is_not_a_fork,
    assert_modes_are_as_agreed,
    assert_no_base_line_left_undeclared,
    assert_pre_push_premise,
    assert_render_round_trips,
)
from lab_commons.dev.hook_install import PROTECTED, hook_installation, hooks_dir, install_command

ROOT = Path(__file__).resolve().parents[1]

#: The command this repo re-renders with. The kit's arm takes it with NO default, because the remedy
#: is the repo's own -- one lab runs a Makefile target and the other an inline line, and a guessed
#: remedy is a refusal nobody can act on. This repo has no render target, so the line IS the remedy.
RERENDER = (
    "python -c \"import sys, pathlib; sys.path.insert(0, 'tests'); "
    'from _famconfig_delta import DELTAS, PRECOMMIT; '
    'from lab_commons.dev.famconfig import artefact_base, render; '
    "pathlib.Path(PRECOMMIT).write_text(render(artefact_base(PRECOMMIT), DELTAS[PRECOMMIT]), encoding='utf-8')\""
)


class TestTheConfigIsTheFamilysPlusNothing:
    def test_the_declaration_is_a_delta_of_the_base_and_not_a_fork(self) -> None:
        """Driven on the DECLARATION, so a malformed delta is a different answer from a drifted file."""
        assert_delta_is_not_a_fork(artefact=PRECOMMIT, deltas=DELTAS, repo=REPO)

    def test_the_file_on_disk_is_what_the_live_base_and_this_delta_render(self) -> None:
        """THE PROPERTY. Re-rendered from the LIVE base, so an upstream edit moves this verdict."""
        assert_artefact_is_rendered(root=ROOT, artefact=PRECOMMIT, deltas=DELTAS, repo=REPO, rerender_hint=RERENDER)

    def test_no_base_line_left_the_file_without_a_declared_drop(self) -> None:
        """The survey's route in, which does not go through the bytes -- a second way to see a loss."""
        assert_no_base_line_left_undeclared(root=ROOT, artefact=PRECOMMIT, repo=REPO)

    def test_the_mode_this_repo_agreed_to_is_the_one_it_is_judged_under(self) -> None:
        """RENDERED is byte equality; a base silently arriving as REQUIRED changes what green means."""
        assert_modes_are_as_agreed(modes={PRECOMMIT: RENDERED})

    def test_the_eleven_core_ids_survive_in_the_file_rather_than_only_in_the_declaration(self) -> None:
        """Read off the LIVE artefact. This repo adds no id, so the family core IS its whole named set."""
        assert_declared_ids_survive(
            config=ROOT / PRECOMMIT, extra_hook_ids=HOOK_ID_CORE, hook_floor=PRECOMMIT_HOOK_FLOOR
        )

    def test_this_repo_adds_nothing_to_the_base_and_the_ceiling_says_so(self) -> None:
        """THE RATCHET'S OTHER SIDE. An empty delta is a decision, and the next line must raise the ceiling."""
        assert PRECOMMIT_DELTA.added == ()
        assert PRECOMMIT_DELTA.anchored == {}
        assert PRECOMMIT_DELTA.dropped == {}
        assert PRECOMMIT_DELTA.ceiling == 0, (
            'a nonzero ceiling on an empty delta is headroom nobody asked for; this repo runs no hook '
            'of its own, so the first line added has to raise this in the same edit and say what for'
        )

    def test_a_planted_edit_to_a_base_line_is_refused_and_the_refusal_names_it(self, tmp_path: Path) -> None:
        """THE PLANTED CONTROL, both directions, through the same function the property uses.

        The bent line is the `pre-commit-hooks` pin, which is the one base line this family has
        actually disagreed about -- v6.0.0 against two consumers' v5.0.0 -- so a control on it is a
        control on the line most likely to be edited back by hand.
        """
        assert_render_round_trips(
            artefact=PRECOMMIT,
            deltas=DELTAS,
            repo=REPO,
            scratch=tmp_path,
            edit=('rev: v6.0.0', '    rev: v5.0.0'),
        )


class TestEveryDeclaredStageIsWired:
    """The half a configuration cannot answer for itself, and the half that was open in both labs."""

    def test_every_stage_this_config_declares_has_a_live_shim_where_git_looks(self) -> None:
        """THE ARM THAT WOULD HAVE CAUGHT THE LABS' GAP, run by the kit on the kit.

        PROTECTED is granted only when every declared stage has a hook file that pre-commit generated
        AND that names this configuration. A `commitizen` declared at `commit-msg` with no
        `commit-msg` shim is UNPROTECTED here and reads as green nowhere.
        """
        report = hook_installation(ROOT)
        assert report.config is not None, 'no .pre-commit-config.yaml at the root -- nothing was checked'
        assert report.stages, 'the configuration declared no stage at all, so this arm looked nowhere'
        assert report.verdict == PROTECTED, (
            'declared stages without a live hook: '
            + '; '.join(f'{stage.stage}: {stage.status} -- {stage.detail}' for stage in report.failing)
            + f'. Install them with `python {" ".join(install_command(ROOT))}`.'
        )

    def test_the_installed_stages_are_the_named_set_this_repo_declared(self) -> None:
        """BOTH DIRECTIONS. A stage arriving unannounced is as wrong as one going missing."""
        live = {stage.stage for stage in hook_installation(ROOT).stages}
        assert live == set(INSTALLED_STAGES), (
            f'this checkout declares stages {sorted(live)} against a recorded {list(INSTALLED_STAGES)}. '
            f'Both directions matter: a new stage is a new thing running on every commit, and a missing '
            f'one is a check nobody is performing. Update `_famconfig_delta.INSTALLED_STAGES` with why.'
        )

    def test_this_repo_has_no_pre_push_hook_and_the_narrowing_rests_on_that(self) -> None:
        """PINNED IN BOTH DIRECTIONS, and the premise is the repo's verdict rather than its config.

        `make verify` is this repo's whole verdict; a pre-push hook re-running it would block every
        push, which is the shape the family kills on sight. Installing one is the right moment to
        re-decide `default_stages`, not a thing to discover later as a miss.
        """
        assert_pre_push_premise(hooks_dir=hooks_dir(ROOT), pre_push_installed=False)

    def test_the_install_command_is_derived_from_the_config_rather_than_spelled(self) -> None:
        """A RESTATED STAGE LIST IS THE DEFECT `install_command` EXISTS FOR.

        motronics' Makefile named two of three stages and every `stages: [pre-commit]` hook went
        uninstalled behind a populated hooks directory. So the remedy this suite prints is READ from
        the config, and this pins that it reaches every stage the config declares.
        """
        argv = install_command(ROOT)
        named = {argv[index + 1] for index, arg in enumerate(argv) if arg == '-t'}
        assert named == set(INSTALLED_STAGES), f'the derived install command reaches {sorted(named)}'


def test_the_base_this_repo_adopted_is_still_the_family_one() -> None:
    """A FLOOR under every assertion above: the base still carries the eleven-id core it is measured as."""
    base = artefact_base(PRECOMMIT)
    assert len(HOOK_ID_CORE) == PRECOMMIT_HOOK_FLOOR, 'the family hook core changed size; re-measure the floor'
    missing = [hook_id for hook_id in HOOK_ID_CORE if not any(f'- id: {hook_id}' in line for line in base.lines)]
    assert missing == [], f'the base no longer renders {missing}, so every id assertion above got thinner'


@pytest.mark.parametrize('artefact', ['.gitignore', 'Makefile'])
def test_the_two_unadopted_bases_are_named_rather_than_silently_missing(artefact: str) -> None:
    """The absence is DECLARED. If either is adopted, this reds and the completeness arm goes in."""
    assert artefact not in DELTAS, (
        f'{artefact} now has a delta here, so this repo has adopted it. Add '
        f'`assert_every_base_is_accounted_for` to this module and delete this row -- that arm is the '
        f'one checking the kit bases and this repo declarations are one set, exactly.'
    )
