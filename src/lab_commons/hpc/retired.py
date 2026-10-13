"""RETIRED SPELLINGS of the dev and test-run surface, each with its replacement -- the one place they may appear.

The noun that named three things (forge plan P1, user ruling 2026-10-09) was split into a RUN (one
execution of a test set on one platform at one sha; its record is ``run-<sha>-<platform>-<req>.json.gz``),
a PART (a platform's result for a sha, composed of runs) and an ADMISSION (computed, never stored).
No alias survives: a command line still using a retired word is REFUSED BY NAME with the remedy
(:func:`refusal`), and ``tests/test_hpc_retired.py`` reds on any retired spelling anywhere else in
the tracked tree, prose included.
"""

from __future__ import annotations

from typing import Final

__all__ = ['RETIRED', 'refusal']

#: Retired spelling -> its replacement. Keys are matched as plain substrings of tracked text.
RETIRED: Final[dict[str, str]] = {
    # Nobody runs the gate to push: the forge produces the verdict once per sha (rule rows, 2026-10-11).
    'ONE-RUN-AFTER-INTEGRATION': 'VERDICTS-FROM-THE-FORGE (the forge serve producer runs the full suite, once per sha)',
    'SUBAGENT-NO-HEAVY-NO-PUSH': 'SUBAGENT-NO-PUSH (a full tier refuses everyone outside the producer)',
    'SUBAGENT_NO_HEAVY_NO_PUSH': 'lab_commons.dev._deny_subagent_row.SUBAGENT_NO_PUSH',
    'lab_commons.hpc verdict': 'python -m lab_commons.hpc run submit|gather (a run, not a verdict)',
    'lab_commons.hpc.verdict': 'lab_commons.hpc.run',
    'submit_verdict': 'lab_commons.hpc.run.submit_run',
    'gather_verdict': 'lab_commons.hpc.run.gather_run',
    'VerdictSpec': 'lab_commons.hpc.shell.RunSpec',
    'record-linux': 'python -m lab_commons.dev.platformparts post <run-record> (platform and scope come from it)',
    'platformparts record': (
        'python -m lab_commons.dev.platformparts post <run-record> (a part is a tests/<platform> status on the forge, '
        'not a ledger row)'
    ),
    'run_part': 'lab_commons.dev.platformparts.part_of (a record proves a Part, posted as a status)',
    'parts_for': 'lab_commons.dev.platformparts.parts_of (the parts of a commit, read from its forge statuses)',
    'record_gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
    '--trunk-tier': 'nothing: the trunk admits parts of scope full read from the forge (lab_commons.dev.admission)',
    'admission --ledger': 'nothing: admission reads the forge, never the local ledger',
    'admission --gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
    # A merge's deviations are checked when it is committed; a push does no audit work.
    'unpublished_merges': 'nothing: python -m lab_commons.dev.mergeaudit <msg-file> audits the merge being committed',
    'merge_refusals': 'lab_commons.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
    'mergeaudit.refusals': 'lab_commons.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
    'run submit --history': "nothing: run submit plans from the repository's last record (lab_commons.hpc.learned)",
    # A cluster part is pulled by the cluster and advanced one step per serve tick, never a blocking process.
    'platformparts cluster': 'forge serve on the cluster login node (lab_commons.dev.forgerun advances the run)',
    '<job>.run.json': '<job>.submission.json (the last submission of a job file)',
    # The machine's CPU seat and an environment's mutation seat are two resources with two names.
    'BoxLock': 'lab_commons.dev.cpuseat.CpuSeat (the machine CPU seat, contended by runs only)',
    'boxlock': 'lab_commons.dev.cpuseat',
    'BOX_POOL': 'lab_commons.dev.cpuseat.CPU_POOL',
    'EnvLock': 'lab_commons.dev.envseat.EnvironmentSeat (keyed by one environment prefix)',
    'envlock': 'lab_commons.dev.envseat',
    'hold_the_box': 'lab_commons.dev.boxwait.hold_run_seats (the CPU seat, then the environment seat)',
    'refuse_if_locked': 'nothing: dep.mutate holds the TARGET environment seat and refuses naming its holder',
    'LOCK_UNDECLARED': 'nothing: who may block a change is the target environment seat, not a port answer',
    'lock_holders': 'lab_commons.dev.envseat.EnvironmentSeat.holders(prefix)',
    'assert_the_holders_adapter_names_a_planted_holder': (
        'famtests.depdoor.assert_a_verdict_in_another_environment_does_not_refuse'
    ),
    'assert_this_repo_supplies_both_halves_of_the_door': (
        'famtests.depdoor.assert_this_repo_declares_its_verdict_anchors'
    ),
    'assert_a_port_declaring_neither_renders_both_gaps': (
        'famtests.depdoor.assert_a_port_declaring_no_anchors_renders_the_gap'
    ),
    'BOTH_GAPS': 'famtests.depdoor.UNDECLARED_GAPS',
    # One name for a platform result: a tests/<platform> part, read through platformparts.standing.
    'lab/gate': 'tests/<platform> (a part, lab_commons.dev.platformparts)',
    'lab/heavy': 'tests/<platform> parts of scope full (what the trunk admits)',
    'GATE_CONTEXT': 'lab_commons.dev.platformparts.part_context(platform)',
    'HEAVY_CONTEXT': 'lab_commons.dev.platformparts.part_context(platform)',
    'gate_context': 'nothing: the queue reads tests/<platform> parts, not a declared context',
    'heavy_context': 'nothing: the queue reads tests/<platform> parts, not a declared context',
    'gate_ready': 'lab_commons.dev.integrator.lane_ready',
    'HEAVY_NEEDED': 'lab_commons.dev.integrator.PARTS_NEEDED',
    'heavy-needed': 'parts-needed',
    'publish_verdict': 'lab_commons.dev.verify.post_part (a settled run posts its platform part)',
    # The local verdict ledger was only a runner cache: it is the run cache now, and decides nothing.
    'verdictledger': 'lab_commons.dev.runcache (the cache) and lab_commons.dev.outcomes (the pytest plugin)',
    'record_promoted': 'lab_commons.dev.runcache.remember',
    'LEDGER_REL': 'lab_commons.dev.runcache.CACHE_REL',
    'output/verdicts/ledger.jsonl': 'output/runcache/runs.jsonl',
    # How a cluster env is built is the commit's own declaration, read at --sha.
    'run submit --install': "the commit's [tool.lab_commons.hpc] install, read at --sha",
    'run submit --needs': "the commit's [tool.lab_commons.hpc] needs, read at --sha",
    # Git hooks are TOML run by one runner in the repo venv (user ruling 2026-10-11): no pre-commit framework.
    '.pre-commit-config.yaml': (
        '[[tool.lab_commons.hooks.<stage>]] in pyproject.toml over lab_commons/dev/githooks/base.toml'
    ),
    'PRE_COMMIT_REMOTE_BRANCH': "git's pre-push lines on stdin (lab_commons.dev.githooks.runner.parse_push_lines)",
    'PRE_COMMIT_FROM_REF': "the remote sha of git's pre-push lines on stdin",
    'PRE_COMMIT_TO_REF': "the local sha of git's pre-push lines on stdin",
    'PRECOMMIT_BASE': 'lab_commons/dev/githooks/base.toml (the hook base, as TOML data)',
    'HOOK_ID_CORE': 'lab_commons.dev.githooks.table.base_rows (the hook base, as TOML data)',
    'hook_requirement': 'nothing: every hook runs in the repo venv, so no second lab-commons is installed',
    'pre_commit install': 'python -m lab_commons.dev.hook_install --install',
    'install_command': 'lab_commons.dev.hook_install.INSTALL_COMMAND',
    'lab-with-venv': 'a hook row run = ["python", ...] (the runner already runs in the repo venv)',
    'lab-issue-ref': 'python -m lab_commons.dev.issueref (the base commit-msg row)',
    'githooks.bootstrap': 'lab_commons.dev.hook_install.shim (finds the checkout venv, execs the runner)',
    'branch-push-only': 'a hook row`s refs regex (pre-push: run only for matching pushed refs)',
    'lab_commons.dev.githooks bump-version': 'python -m lab_commons.dev.githooks script bump-version',
    'lab_commons.dev.githooks branchset-push': 'python -m lab_commons.dev.githooks script branchset-push',
    'lab_commons.dev.githooks cz-push-range': 'python -m lab_commons.dev.githooks script cz-push-range',
    # Where a run is and what the cluster can take are read from the forge, from any machine; no verb asks over ssh.
    'lab_commons.hpc probe': (
        'python -m lab_commons.dev.forge queue (each cluster host card carries its headroom, partitions and quota)'
    ),
    'lab_commons.hpc run status': 'python -m lab_commons.dev.forge queue (each run in flight shows its progress)',
    'lab_commons.hpc run watch': 'forge serve on the cluster login node (it gathers each run it owns, a step a tick)',
    # What a host or account can take is its measured Supply (design: one admission, migration step 2, 2026-10-13).
    # The type is matched by its uses, not the bare word, which the ratchet prose uses in its plain sense.
    'Headroom(': 'lab_commons.compute.model.Supply (from lab_commons.compute.probe)',
    ': Headroom': 'lab_commons.compute.model.Supply',
    ', Headroom': 'lab_commons.compute.model.Supply',
    'box_headroom': 'lab_commons.compute.probe.box (free cores, memory, OS and tools measured now)',
    'no free core': "the admission's lacking resource: 'no headroom: lacking cpu' (lab_commons.compute.model.admit)",
    'lab_commons.hpc.outstanding': 'lab_commons.dev.forgerun (the runs in flight, one state file each, in forge-runs/)',
}


def refusal(prog: str, word: str) -> str | None:
    """The refusal for *word* used as a verb of *prog*, naming its replacement; ``None`` when it is not retired."""
    remedy = RETIRED.get(f'{prog} {word}') or RETIRED.get(word)
    return None if remedy is None else f'`{prog} {word}` is retired: use {remedy}'
