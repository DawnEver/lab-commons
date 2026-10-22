"""RETIRED SPELLINGS of the dev and test-run surface, each with its replacement.

With the compute half (:mod:`lab_commons.hpc._retired_compute`) these are the only places a retired
spelling may appear.

The noun that named three things (forge plan P1, user ruling 2026-10-09) was split into a RUN (one
execution of a test set on one platform at one sha; its record is ``run-<sha>-<part>-<req>.json.gz``),
a PART (a platform's result for a sha, composed of runs) and an ADMISSION (computed, never stored).
No alias survives: a command line still using a retired word is REFUSED BY NAME with the remedy
(:func:`refusal`), and ``tests/test_hpc_retired.py`` reds on any retired spelling anywhere else in
the tracked tree, prose included.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from lab_commons.hpc._retired_compute import COMPUTE

__all__ = ['RETIRED', 'TEST_WORDS', 'refusal', 'refuse_retired_keys', 'retired_in']

_BRANCHSET_ROW: Final = 'the base pre-push row `branchset-push` (lab_commons/dev/githooks/base.toml)'

#: Retired spelling -> its replacement. Keys are matched as plain substrings of tracked text.
RETIRED: Final[dict[str, str]] = {
    **COMPUTE,
    # Only dev leaves lab-commons (user ruling 2026-10-22): what the execution side needs of it stays here.
    'lab_commons.dev.envkey': 'lab_commons.envkey (environment identity is an execution concern)',
    'dev import envkey': 'from lab_commons import envkey',
    '_forge_journal import durable_write': 'from lab_commons.compute.durable import durable_write',
    '_forge_journal.durable_write': 'lab_commons.compute.durable.durable_write',
    'lab_commons.multiprocess': 'nothing: deleted 2026-10-22, it had no importer',
    # Scratch is tracked by git but never committed (user ruling 2026-10-22): a one-off is promoted into src/
    # with tests or deleted; the archive-to-memory exit is gone with no compatibility path.
    'SCRATCH-ARCHIVED-OR-PROMOTED': 'SCRATCH-IS-NEVER-COMMITTED (lab_commons.dev.scratch)',
    'archive_scratch': 'promote into src/ with tests, or python -m lab_commons.dev.scratch discard <path>',
    # A unit lives in the VALUE, never the NAME (user ruling 2026-10-19): the box wait is a pint duration.
    '--lock-wait-s': '--lock-wait "<duration>" (a time quantity such as "30 s"; a bare number is refused)',
    # A model API key is one scheme: its provider's host, username api-key[-<task>] (user ruling 2026-10-19).
    'pr-review.anthropic.lab-commons': "api.anthropic.com's pr-review task key (lab_commons.credentials.api_key)",
    # The forge only orchestrates (plan step 5, 2026-10-20): the review is the repo's declared command, run in the
    # integration step on whichever host runs it; the forge holds no model client, prompt or key.
    'pr_review': 'nothing: the review runs inside the integration step (lab_commons.dev.forgereview.step)',
    'forgereview.anthropic': '[tool.lab_commons.review] command (the method chooses provider and model)',
    'forgereview import anthropic': '[tool.lab_commons.review] command (the method chooses provider and model)',
    'ReviewKeyMissing': 'nothing: the review command reads its own key (ai_lab)',
    'forgereview.review_key': 'nothing: the review command reads its own key (ai_lab)',
    # The forge-sync watch set was named for git's upstream, another thing (2026-10-21): a sync merges FROM it.
    'labUpstream': '`branch.<child>.labSyncFrom` (lab_commons.dev.forgesync.CONFIG_KEY)',
    # The offer sentence judges nothing; it lives beside the request it names (user ruling 2026-10-19 #8).
    'verify.offer_line': 'lab_commons.dev.forgerequest.offer_line',
    'verify import offer_line': 'from lab_commons.dev.forgerequest import offer_line',
    # An env is stale against the commit's OWN lock, never a dependency's remote tip (PIN + FRESHNESS, 2026-10-17).
    'familyheads': 'lab_commons.dev.lockpins (installed git commits against the checkout uv.lock)',
    'family_freshness': 'lab_commons.dev.lockpins.lock_freshness',
    'family-heads.json': 'nothing: the lock check is local and caches no forge head',
    # A session branch is protected for what it IS -- never by a directory or a host; its one writer is git's own
    # one-checkout-per-branch and fast-forward rules (user rulings 2026-10-13/14; lab_commons.dev.sessionbranch).
    'INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT': 'ONE-WRITER-PER-SESSION-BRANCH and INTEGRATION-IS-MERGES-ONLY',
    'PUSH-FROM-ITS-OWN-BRANCH': 'PUBLISH-IS-THE-LOCAL-REF (lab_commons.dev.sessionbranch, at pre-push)',
    'integrationhome': 'lab_commons.dev.sessionbranch (pre-push) and lab_commons.dev.integrate (no working tree)',
    'integration-home': 'the base pre-push row `session-branch` (lab_commons/dev/githooks/base.toml)',
    'push-from-its-own-branch': 'the base pre-push row `session-branch` (lab_commons/dev/githooks/base.toml)',
    'integrator.host': 'nothing: no host owns integration; any host raises its prompts as one forge issue per row',
    # The integration branch moves by the FORGE's merge of a green PR; a client verb is a second writer
    # (user ruling 2026-10-19, INTEGRATION-TAKES-ONLY-FORGE-MERGES).
    'integrate merge': 'the forge merges a green pull request server side (lab_commons.dev.forgemerge)',
    'forge promote': 'python -m lab_commons.dev.integrate promote <sha>, then a plain push of the trunk',
    'forgepromote': 'lab_commons.dev.integrate',
    # All maintenance happens on gitea; GitHub is ONLY a push mirror (user ruling 2026-10-19): nothing reads,
    # carries, comments on or closes a GitHub PR, and auto-merge is gitea-only.
    'forgeintegration import venues': 'nothing: PRs are opened and merged on gitea; GitHub is only a push mirror',
    'forgemirror': 'nothing: GitHub is only a push mirror; open the PR on gitea (lab_commons.dev.forgemerge)',
    'GITHUB-PRS-ARRIVE-ON-THE-FORGE': 'nothing: GitHub is only a push mirror; PRs are opened on gitea',
    'integrator.mirror': 'nothing: GitHub is only a push mirror (gitea Settings > Mirror settings, branch-set.md)',
    'Marker.MIRROR': 'nothing: no comment is written on a GitHub PR; GitHub is only a push mirror',
    'mirror_step': 'nothing: forge serve and forge integration read gitea PRs only; GitHub is only a push mirror',
    'pr/gh-': 'pr/<name> on gitea (the declared ephemeral namespace); GitHub is only a push mirror',
    # Files are an HPC resource: retention runs every serve tick, and a tree goes once its sha is not in flight.
    'every_hours': 'nothing: retention runs every serve tick (lab_commons.dev.retention)',
    'tree-unreferenced': 'tree-not-in-flight (lab_commons.dev.retentionplan.in_flight)',
    # Outcome is the task-result word lab_commons.outcomes owns (plan A1.8, 2026-10-18).
    'retention.Outcome': 'lab_commons.dev.retention.Freed',
    'box.Outcome': 'lab_commons.box.ProcessEnd (its .status maps a kill onto lab_commons.outcomes.Status)',
    'deploy.Outcome': 'lab_commons.supervise.deploy.PhaseResult (its .status reads OK or ERROR)',
    'execute.Outcome': 'lab_commons.compute.execute.Settled',
    'verdict.Outcome': 'lab_commons.outcomes.Conclusion (same tokens: pass, fail, inconclusive)',
    'forgestatus.Status': 'lab_commons.dev.forgestatus.CommitStatus (a forge commit-status record, not a task result)',
    'forgestatus import Status': 'from lab_commons.dev.forgestatus import CommitStatus',
    # A round streams into ONE file, kept as one file per run (HPC one-file plan, phase 5, 2026-10-18).
    'pack_streams': 'lab_commons.hpc.records.kept_text (the round file, its retired shape upgraded on read)',
    'KEPT_STREAMS': 'lab_commons.hpc.records.stream_sources (``runs/<sha>/streams.jsonl``)',
    # A request is a job on the part path: admitted by its declared need-set and run (lab-commons 2026-10-13).
    'claims requests yet': 'lab_commons.dev.forgerequest (admitted by its declared need-set, claimed and run)',
    # Nobody runs the gate to push: the forge produces the verdict once per sha (rule rows, 2026-10-11).
    'ONE-RUN-AFTER-INTEGRATION': 'VERDICTS-FROM-THE-FORGE (the forge serve producer runs the full suite, once per sha)',
    'SUBAGENT-NO-HEAVY-NO-PUSH': 'SUBAGENT-NO-PUSH (a full tier refuses everyone outside the producer)',
    'SUBAGENT_NO_HEAVY_NO_PUSH': 'lab_commons.dev._deny_subagent_row.SUBAGENT_NO_PUSH',
    'lab_commons.hpc verdict': 'python -m lab_commons.hpc run submit|gather (a run, not a verdict)',
    'lab_commons.hpc.verdict': 'lab_commons.hpc.run',
    'submit_verdict': 'lab_commons.hpc.run.submit_run',
    'gather_verdict': 'lab_commons.hpc.gather.gather_run',
    'VerdictSpec': 'lab_commons.hpc.shell.RunSpec',
    'record-linux': 'python -m lab_commons.dev.platformparts post <run-record> (platform and scope come from it)',
    'platformparts record': (
        'python -m lab_commons.dev.platformparts post <run-record> (a part is a tests/<part> status on the forge, '
        'not a ledger row)'
    ),
    'run_part': 'lab_commons.dev.platformparts.part_of (a record proves a Part, posted as a status)',
    'parts_for': 'lab_commons.dev.platformparts.parts_of (the parts of a commit, read from its forge statuses)',
    'record_gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
    '--trunk-tier': 'nothing: the trunk admits parts of scope full read from the forge (lab_commons.dev.pushgate)',
    'pushgate --ledger': 'nothing: the push gate reads the forge, never the local ledger',
    'pushgate --gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
    # A merge's deviations are checked when it is committed; a push does no audit work.
    'unpublished_merges': 'nothing: python -m lab_commons.dev.mergeaudit <msg-file> audits the merge being committed',
    'merge_refusals': 'lab_commons.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
    'mergeaudit.refusals': 'lab_commons.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
    'run submit --history': "nothing: run submit plans from the repository's last record (lab_commons.compute.learned)",
    # A cluster part is pulled by the cluster and advanced one step per serve tick, never a blocking process.
    'platformparts cluster': 'forge serve on the cluster login node (lab_commons.dev.forgerun advances the run)',
    '<job>.run.json': '<job>.submission.json (the last submission of a job file)',
    # One name for a platform result: a tests/<part> part, read through platformparts.standing.
    'lab/gate': 'tests/<part> (a part, lab_commons.dev.platformparts)',
    'lab/heavy': 'tests/<part> parts of scope full (what the trunk admits)',
    'GATE_CONTEXT': 'lab_commons.dev.platformparts.part_context(platform)',
    'HEAVY_CONTEXT': 'lab_commons.dev.platformparts.part_context(platform)',
    'gate_context': 'nothing: the queue reads tests/<part> parts, not a declared context',
    'heavy_context': 'nothing: the queue reads tests/<part> parts, not a declared context',
    'gate_ready': 'lab_commons.dev.integrator.lane_ready',
    'HEAVY_NEEDED': 'lab_commons.dev.integrator.PARTS_NEEDED',
    'heavy-needed': 'parts-needed',
    'publish_verdict': 'lab_commons.dev.verify.post_part (a settled run posts its platform part)',
    # The local verdict ledger was only a runner cache: it is the run cache now, and decides nothing.
    'verdictledger': 'lab_commons.dev.runcache (the cache) and lab_commons.dev.test_outcomes (the pytest plugin)',
    'record_promoted': 'lab_commons.dev.runcache.remember',
    'LEDGER_REL': 'lab_commons.dev.runcache.CACHE_REL',
    'output/verdicts/ledger.jsonl': 'output/runcache/record.jsonl',
    # The run cache is an outcomes Record in its own file (plan A1.5): a retired-code checkout keeps its old file.
    'runcache/runs.jsonl': 'output/runcache/record.jsonl (lab_commons.dev.runcache.CACHE_REL)',
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
    # The base pre-push row runs the check in the repo venv; a shell wrapper hunting an interpreter is baggage.
    'lab_commons.dev.githooks branchset-push': _BRANCHSET_ROW,
    'githooks script branchset-push': _BRANCHSET_ROW,
    'branchset-push.sh': _BRANCHSET_ROW,
    'lab_commons.dev.githooks cz-push-range': 'python -m lab_commons.dev.githooks script cz-push-range',
    # A session share is temporary: derived from Slurm or one expiring forge record, never a machine file (2026-10-17).
    '[hpc.session.': 'python -m lab_commons.hpc allocate (one expiring forge record), else the derived share',
    # Where a run is and what the cluster can take are read from the forge, from any machine; no verb asks over ssh.
    'lab_commons.hpc probe': (
        'python -m lab_commons.dev.forge queue (each cluster host card carries its supply, partitions and quota)'
    ),
    'lab_commons.hpc run status': 'python -m lab_commons.dev.forge queue (each run in flight shows its progress)',
    'lab_commons.hpc run watch': 'forge serve on the cluster login node (it gathers each run it owns, a step a tick)',
    # A local verify posts no part: only the producer forge serve spawned does (VERDICTS-FROM-THE-FORGE, 2026-10-18).
    '--no-status': 'nothing: a local verify never posts; only the producer forge serve spawned posts its part',
    'card.delegates': 'lab_commons.dev.forgehosts.card_owners (a card is vouched for by its forge-recorded author)',
    # A tool is present when found, at the declared version, smoke-tested and not failed since (D21, 2026-10-16).
    'TOOL_PROBES': 'lab_commons.compute.toolchecks.CHECKS (find, version and smoke per tool)',
    'femm_found': 'lab_commons.compute.toolchecks.femm_find (one step of the femm check)',
    'matlab_found': 'lab_commons.compute.toolchecks.matlab_find (one step of the matlab check)',
    'simba_found': 'lab_commons.compute.toolchecks.simba_find (one step of the simba check)',
    'card.provides': 'lab_commons.dev.forgehosts.HostCard.tools (each tool as probed, with its evidence)',
    # Issues never rot (user ruling 2026-10-15): every tidy rule is on, forge serve runs it, a kind is a Marker.
    'forgetidy.SUBJECT': 'lab_commons.dev.forgepulls.Marker.TIDY (every marker kind is a Marker member)',
    'forgeintegration.SUBJECT': 'lab_commons.dev.forgepulls.Marker.INTEGRATION (every marker kind is a Marker member)',
    'hygiene.any': 'nothing: every forge tidy rule is on; a repo only tunes [tool.lab_commons.forge.issue_hygiene]',
    'run by `forge integration`': 'forge serve runs forge tidy on every host every tick (lab_commons.dev.forgeserve)',
    # A lock-consuming door is judged against the COMMITTED lock: the old name described the untracked era.
    'REVERTS': 'UNLOCKED (lab_commons.dev.installdoor.Delivery.UNLOCKED)',
    'RevertingDoorsError': 'lab_commons.dev.installdoor.UnlockedDoorsError',
    'reverting(': 'lab_commons.dev.installdoor.unlocked(',
    # PIN + FRESHNESS (user ruling 2026-10-17): the lock is committed and the developer who pushes keeps it at the
    # newest resolution (lab_commons.dev.lockcheck). No lock AGE, no scheduled re-lock, no tip folded into a key.
    'lockupgrade': 'nothing: the `lock` pre-push row refuses a stale lock; the pusher runs `dep --lock-upgrade`',
    'Lock-Upgraded': 'nothing: freshness is judged by resolving, never by a commit trailer',
    'integrator.lock_max_age_days': 'nothing: delete the key -- a lock is judged by freshness, not age',
    'branchset.update': 'nothing: delete the key -- no lock upgrade branch exists; the pusher upgrades',
    'update/lock': 'nothing: no lock upgrade branch exists; the pusher upgrades',
    'requests.lock_upgrade': 'nothing: delete the request -- the `lock` pre-push row refuses a stale lock',
    'floating_tips': "lab_commons.hpc.builds.builds_at (the committed lock's hash keys the venv)",
    'floating_git': "nothing: a git dependency's revision is the lock's (lab_commons.hpc.builds.builds_at)",
    'remote_tip': "nothing: a git dependency's revision is the lock's (lab_commons.hpc.builds.builds_at)",
    'overlay_argv': 'nothing: a served venv is exactly its committed lock; nothing is installed over it',
    # An HPC job ships ONE file and leaves a few (plan 2026-10-17): one artifact per sha, one results file per run.
    'files_needed': 'lab_commons.hpc.plan.FILES_PER_RUN (one results file per run: its width adds no file)',
    'FILES_PER_SHARD': 'lab_commons.hpc.plan.FILES_PER_RUN (one results file per run: its width adds no file)',
    'occupied_trees': 'lab_commons.dev.retentionplan.occupied_shas (a test run tree or a job run directory)',
    '--output=logs/': 'nothing: a task writes to node-local $TMPDIR; its tail is kept in results.jsonl',
    'reuse that tree and env': 'run build builds the commit artifact, one file (lab_commons.hpc.artifact)',
    # A test's result is an outcomes Cell under its Expectation (plan A1.1/A1.2, user rulings 2026-10-18).
    'not-covered': "nothing: a handed id is no cell; the run record's `handed` names its part",
    'NOT_COVERED': "nothing: a handed id is no cell; the run record's `handed` names its part",
    'UNCARRIED': 'nothing: lab_commons.hpc.records.held_cells offers DECIDED cells only',
    'held_outcomes': 'lab_commons.hpc.records.held_cells',
    'lab_commons.dev.outcomes': 'lab_commons.dev.test_outcomes (a Record of test cells per process)',
    'read_reasons': 'lab_commons.dev.test_outcomes.read_cells (a reason travels in its cell)',
    'OutcomeRecorder': 'lab_commons.dev.test_outcomes.CellRecorder',
    'outcome_of(': 'lab_commons.hpc.pytest_item.judged (the one pytest mapping, to a plain test cell)',
}

#: THE RETIRED PER-TEST WORDS, and only for LIVE state -- a cluster run's ``state.json`` and the stream of a
#: round in flight across the change (FORMATS-UPGRADE-ON-READ): each word -> ``(status word, reason kind)`` it
#: now reads as (:func:`lab_commons.hpc.testcells.upgraded`; a status word is ``TestCell.word``'s spelling).
#: ``None``: a handed id, which is no cell. A regenerable artefact in these words is refused, never upgraded.
TEST_WORDS: Final[dict[str, tuple[str, str] | None]] = {
    'passed': ('ok', ''),
    'failed': ('rejected', 'assertion'),
    'error': ('error', 'error'),
    'skipped': ('not_applicable', 'skipped'),
    'xfailed': ('rejected/expected-rejected', 'expected'),
    'lost': ('infra', 'lost'),
    'missing': ('pending', ''),
    'not-covered': None,
}


def _pattern(spelling: str) -> re.Pattern[str]:
    """A FLAG spelling (leading ``-``) matches as a whole token, so ``--mode`` is not in ``--model``."""
    escaped = re.escape(spelling)
    return re.compile(rf'(?<![\w-]){escaped}(?![\w-])' if spelling.startswith('-') else escaped)


_PATTERNS: Final = {spelling: _pattern(spelling) for spelling in RETIRED}


def retired_in(text: str) -> list[str]:
    """Every retired spelling *text* carries, in registry order (flags as whole tokens)."""
    return [spelling for spelling, pattern in _PATTERNS.items() if pattern.search(text)]


def refusal(prog: str, word: str) -> str | None:
    """The refusal for *word* used as a verb of *prog*, naming its replacement; ``None`` when it is not retired."""
    remedy = RETIRED.get(f'{prog} {word}') or RETIRED.get(word)
    return None if remedy is None else f'`{prog} {word}` is retired: use {remedy}'


def refuse_retired_keys(table: str, keys: Iterable[str]) -> None:
    """Refuse every key of machine-file ``[table]`` that is a retired spelling, naming each remedy."""
    found = [f'[{table}] {key} is retired: {RETIRED[key]}' for key in keys if key in RETIRED]
    if found:
        msg = 'the machine file: ' + '; '.join(found)
        raise ValueError(msg)
