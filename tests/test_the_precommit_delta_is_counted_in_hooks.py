r"""THE `.pre-commit-config.yaml` DELTA IS COUNTED IN HOOKS, AND COUNTING IT IN LINES INVERTS THE ANSWER.

WHAT THIS ARM SETTLES. `measured_delta` sizes motronics-studio's pre-commit delta at 87 added lines
against a 21-line base, and `Delta.ceiling` says in its own docstring that the number is "the point
at which this repo's delta has stopped being a delta". Read literally, 87-against-21 is a repo that
has forked the artefact, and the R2 adoption plan recorded it as the strain that might refuse the
adoption outright.

IT IS A UNIT ERROR, AND THE MEASUREMENT THAT CONVICTS IT IS THE RANK INVERSION BELOW. A `.gitignore`
pattern and a Makefile recipe line are ONE line per decision, so for those artefacts a line count IS
a decision count and `ceiling` means what it says. A pre-commit hook is a YAML MAPPING: one decision
costs an `- id:` plus however many of `name`, `entry`, `args`, `language`, `stages`,
`additional_dependencies`, `files`, `exclude`, `pass_filenames`, `always_run` and `require_serial`
it needs. So the same delta measures differently depending only on how the hooks are SOURCED -- a
lab pulling extra hooks from an upstream repo pays one line each, a repo declaring `- repo: local`
hooks pays ten to twelve.

MEASURED 2026-09-18 over the three consumers, and the two orders DISAGREE:

    repo               line delta   own hooks   lines per own hook
    wdg-lab                    38          11                 3.5
    optimi-lab                  8           8                 1.0
    motronics-studio           87           7                12.4

By lines motronics is the largest delta in the family by a factor of two. By HOOKS it is the
SMALLEST of the three consumers. Nothing about motronics' relationship to the base changed between
those two readings -- all 11 core ids are present, and 19 of the 21 base lines match literally, the
two that do not being `rev:` pins. The ranking moved because the unit did.

SO THE VERDICT IS: NOT PAST THE CEILING, and the artefact IS adoptable for motronics-studio. The
seam is exactly the one `Delta.anchored` was added for -- the `stages:`/`exclude:`/`args:` lines hang
on BASE hook ids and anchor onto them, and the 7 local hooks append. What it is NOT is a free
adoption: the 2 `rev:` pins are a real upstream version bump (`v5.0.0` -> `v6.0.0`, `v4.6.0` ->
`v4.13.9`) that changes what runs on every commit in that repo, so it belongs in its own commit with
that stated. This suite does not perform it; it records the size the adoption is, in the unit the
ceiling has to be stated in, so the next lane cannot re-open the question from the line count alone.

THE RATCHET HAS TWO SIDES. A repo growing a hook reds here, and so does one LOSING one -- the
recorded set is exact, not a subset -- because a local hook silently dropped is a verdict that
stopped running while the config still reads as guarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _config_census import hook_ids, reachable_repos
from _config_census_rows import HOOK_ID_CORE, PRECOMMIT_LINE_DELTA, PRECOMMIT_OWN_HOOKS, REPO_PATHS

from lab_commons.dev.famconfig import VacuousBase, artefact_base, declared_hooks, hook_delta, measured_delta

#: The artefact under measurement, spelled once.
PRECOMMIT = '.pre-commit-config.yaml'

#: How many repos must be readable before a comparison between them means anything. TWO is the least
#: that can carry a disagreement between two orderings; with one there is no order at all.
CONSUMER_FLOOR = 2


def _own_hooks(root: Path) -> tuple[str, ...]:
    """The hook ids *root* declares beyond the family core, read off the live file."""
    return tuple(sorted(hook_ids(root) - set(HOOK_ID_CORE)))


class TestTheUnitIsTheHook:
    def test_a_hook_spelled_across_a_dozen_attribute_lines_still_counts_as_one(self) -> None:
        """PLANTED, driving BOTH real readers: the line count says twelve, the hook count says one."""
        base = artefact_base(PRECOMMIT)
        extra = (
            '  - repo: local',
            '    hooks:',
            '      - id: planted-verdict',
            '        name: one decision, twelve lines',
            '        entry: lab-with-venv',
            "        args: ['scripts/planted.py']",
            '        language: python',
            "        additional_dependencies: ['lab-commons[dev]']",
            '        always_run: true',
            '        pass_filenames: false',
            '        require_serial: true',
            '        verbose: true',
            '        stages: [pre-push]',
        )
        planted = '\n'.join([*base.lines, '', *extra, ''])
        assert hook_delta(planted, HOOK_ID_CORE, floor=len(HOOK_ID_CORE)) == ('planted-verdict',)
        priced = tuple(line for line in extra if line.rstrip() not in base.content_lines)
        assert len(priced) == 12, f'the plant was meant to cost twelve lines and cost {len(priced)}: {priced}'

    def test_a_config_that_parsed_to_nothing_is_refused_rather_than_reported_as_an_empty_delta(self) -> None:
        """THE FLOOR. A YAML the reader could not parse looks exactly like a repo adding no hook."""
        with pytest.raises(VacuousBase):
            hook_delta('# every line a comment\n', HOOK_ID_CORE, floor=len(HOOK_ID_CORE))

    def test_the_reader_is_blind_to_yaml_key_order(self) -> None:
        """MEASURED: wdg-lab writes `repo: local` AFTER its `hooks:` block, so a positional scan misses it."""
        trailing = '  - hooks:\n      - id: after-the-fact\n    repo: local\n'
        assert declared_hooks(trailing) == ('after-the-fact',)

    def test_a_commented_out_hook_is_not_a_hook_the_repo_declares(self) -> None:
        """optimi-lab carries a whole commented-out local block; counting it would invent a delta."""
        assert declared_hooks('  - hooks:\n      - id: live\n    # - id: dead\n') == ('live',)


class TestTheRepoValuesAreReMeasured:
    def test_every_reachable_repo_declares_exactly_the_own_hooks_its_row_records(self) -> None:
        """Both sides: a hook arriving reds, and a hook DISAPPEARING reds. Exact, never a subset."""
        reached = reachable_repos(REPO_PATHS)
        assert len(reached) >= CONSUMER_FLOOR, f'only {sorted(reached)} readable; this arm measured nothing'
        for repo, root in reached.items():
            assert _own_hooks(root) == PRECOMMIT_OWN_HOOKS[repo], (
                f'{repo} declares own hooks {_own_hooks(root)}, recorded {PRECOMMIT_OWN_HOOKS[repo]}. A '
                f'hook ADDED here raises this repo -- say so and raise the recorded set; a hook GONE is '
                f'a verdict that stopped running while the config still reads as guarded.'
            )

    def test_the_line_delta_and_the_hook_delta_rank_the_consumers_differently(self) -> None:
        """THE FINDING, re-derived: the same deltas, two units, two opposite orders."""
        reached = {r: root for r, root in reachable_repos(REPO_PATHS).items() if PRECOMMIT_OWN_HOOKS[r]}
        assert len(reached) >= CONSUMER_FLOOR, f'only {sorted(reached)} carry a delta; there is no order to compare'
        base = artefact_base(PRECOMMIT)
        lines = {repo: len(measured_delta(root / PRECOMMIT, base, repo).added) for repo, root in reached.items()}
        hooks = {repo: len(_own_hooks(root)) for repo, root in reached.items()}
        for repo, recorded in lines.items():
            assert recorded == PRECOMMIT_LINE_DELTA[repo], (
                f'{repo} measures a LINE delta of {recorded}, recorded {PRECOMMIT_LINE_DELTA[repo]}. Both '
                f'numbers in this module are measurements; re-take the pair rather than editing one digit.'
            )
        by_lines = max(lines, key=lambda r: lines[r])
        by_hooks = max(hooks, key=lambda r: hooks[r])
        assert by_lines != by_hooks, (
            f'the largest delta is {by_lines} by lines and {by_hooks} by hooks. The two units agreeing is '
            f'not a pass -- it means the family configs converged on one sourcing style, and the evidence '
            f'that the ceiling cannot be stated in lines has to be re-taken before this arm is trusted '
            f'again. Re-measure and rewrite this module docstring table.'
        )

    def test_motronics_is_the_largest_by_lines_and_the_smallest_by_hooks(self) -> None:
        """The named DIRECTION of the inversion, so a mere swap cannot satisfy the arm above."""
        reached = reachable_repos(REPO_PATHS)
        if 'motronics-studio' not in reached:
            pytest.fail('motronics-studio is not checked out beside this repo, so the R2 verdict is unmeasured')
        consumers = {r: root for r, root in reached.items() if PRECOMMIT_OWN_HOOKS[r]}
        base = artefact_base(PRECOMMIT)
        lines = {r: len(measured_delta(root / PRECOMMIT, base, r).added) for r, root in consumers.items()}
        hooks = {r: len(_own_hooks(root)) for r, root in consumers.items()}
        assert lines['motronics-studio'] == max(lines.values()), lines
        assert hooks['motronics-studio'] == min(hooks.values()), hooks
