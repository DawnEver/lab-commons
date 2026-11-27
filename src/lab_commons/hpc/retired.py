"""RETIRED SPELLINGS of the dev and test-run surface, each with its replacement.

With the compute half (:mod:`lab_commons.hpc._retired_compute`) these are the only places a retired
spelling may appear.

The noun that named three things (forge plan P1, user ruling 2026-10-09) was split into a RUN (one
execution of a test set on one platform at one sha; its record is ``run-<sha>-<part>-<req>.json.gz``),
a PART (a platform's result for a sha, composed of runs) and an ADMISSION (computed, never stored).
No alias survives: a command line still using a retired word is REFUSED BY NAME with the remedy
(:func:`refusal`), and ``tests/test_hpc_retired.py`` reds on any retired spelling anywhere else in
the tracked tree, prose included. Matching and the refusal sentence are the family mechanism's,
:class:`lab_commons.retired.Registry` (:data:`REGISTRY`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from lab_commons.hpc._retired_compute import COMPUTE, classified
from lab_commons.retired import Registry, Retired

__all__ = [
    'CLASSIFIED',
    'MOVED_TABLES',
    'REGISTRY',
    'RETIRED',
    'TEST_WORDS',
    'refusal',
    'refuse_retired_keys',
    'retired_in',
]

_BRANCHSET_ROW: Final = 'the base pre-push row `branchset-push` (dev_lab/dev/githooks/base.toml)'

#: The tables dev-lab alone reads, moved to ``[tool.dev_lab]`` with no fallback (plan A3 2026-10-27).
MOVED_TABLES: Final = (
    'areas', 'automode', 'branchset', 'carry', 'ci', 'components', 'dep', 'doors', 'ephemeral_refs', 'forge',
    'hooks', 'integrator', 'parts', 'placement', 'requests', 'review', 'stoprun', 'verify', 'worktrees',
)  # fmt: skip

#: Retired spelling -> (replacement, decision, class -- dev_lab.dev.retirementclass). Keys are matched as plain
#: substrings of tracked text.
# fmt: off
CLASSIFIED: Final[dict[str, tuple[str, str, str]]] = {
    **COMPUTE,
    **classified(
        'Only dev leaves lab-commons (user ruling 2026-10-22): what the execution side needs of it stays here.', {
        'lab_commons.dev.envkey': 'lab_commons.envkey (environment identity is an execution concern)',
        'dev import envkey': 'from lab_commons import envkey',
        '_forge_journal import durable_write': 'from lab_commons.compute.durable import durable_write',
        '_forge_journal.durable_write': 'lab_commons.compute.durable.durable_write',
        'lab_commons.multiprocess': 'nothing: deleted 2026-10-22, it had no importer',
    }),
    **classified(
        "v1 rulings D125/D126 (user 2026-11-26): one unit door; pint<->pydantic types are retired with pydantic.", {
        'lab_commons.units': (
            'lab_commons.quantity (parse_si / to_si / from_si / display / registry): a value carries its unit and '
            'crosses once there'
        ),
    }),
    **classified(
        "v1 ruling D126 (user 2026-11-26): a domain vocabulary is the product's own.", {
        'lab_commons.em': (
            "nothing: a domain vocabulary is the product's own; values carry units and cross at lab_commons.quantity"
        ),
    }, lost='capability-dropped'),
    **classified(
        'ENV-IS-READ-ONCE (family policy 2026-11-25): the machine-home override is read in the host entry alone.', {
        'home.HOME_ENV': 'lab_commons.hostenv.HOME_ENV (the host entry reads it; home() takes the record)',
        'lab_commons.home import HOME_ENV': 'from lab_commons.hostenv import HOME_ENV',
    }),
    **classified(
        'Scratch is tracked by git but never committed (user ruling 2026-10-22): a one-off is promoted into src/ '
        'with tests or deleted; the archive-to-memory exit is gone with no compatibility path.', {
        'SCRATCH-ARCHIVED-OR-PROMOTED': 'SCRATCH-IS-NEVER-COMMITTED (dev_lab.dev.scratch)',
        'archive_scratch': 'promote into src/ with tests, or python -m dev_lab.dev.scratch discard <path>',
    }),
    **classified(
        'A unit lives in the VALUE, never the NAME (user ruling 2026-10-19): the box wait is a pint duration.', {
        '--lock-wait-s': '--lock-wait "<duration>" (a time quantity such as "30 s"; a bare number is refused)',
    }),
    **classified(
        "A model API key is one scheme: its provider's host, username api-key[-<task>] (user ruling 2026-10-19).", {
        'pr-review.anthropic.lab-commons': "api.anthropic.com's pr-review task key (lab_commons.credentials.api_key)",
    }),
    **classified(
        "The task set is the caller's data, never a constant here (user ruling 2026-11-05): ai-lab's routes.toml.", {
        'API_KEY_TASKS': 'ai_lab.client.tasks() (a task is any lab_commons.credentials.TASK_NAME the caller declares)',
    }),
    **classified(
        "The forge only orchestrates (plan step 5, 2026-10-20): the review is the repo's declared command, run in"
        ' the integration step on whichever host runs it; the forge holds no model client, prompt or key.', {
        'pr_review': 'nothing: the review runs inside the integration step (dev_lab.dev.forgereview.step)',
        'forgereview.anthropic': '[tool.dev_lab.review] command (the method chooses provider and model)',
        'forgereview import anthropic': '[tool.dev_lab.review] command (the method chooses provider and model)',
        'ReviewKeyMissing': 'nothing: the review command reads its own key (ai_lab)',
        'forgereview.review_key': 'nothing: the review command reads its own key (ai_lab)',
    }),
    **classified(
        "The forge-sync watch set was named for git's upstream, another thing (2026-10-21): a sync merges FROM "
        'it.', {
        'labUpstream': '`branch.<child>.labSyncFrom` (dev_lab.dev.forgesync.CONFIG_KEY)',
    }),
    **classified(
        'The offer sentence judges nothing; it lives beside the request it names (user ruling 2026-10-19 #8).', {
        'verify.offer_line': 'dev_lab.dev.forgerequest.offer_line',
        'verify import offer_line': 'from dev_lab.dev.forgerequest import offer_line',
    }),
    **classified(
        "An env is stale against the commit's OWN lock, never a dependency's remote tip (PIN + FRESHNESS, "
        '2026-10-17).', {
        'familyheads': 'dev_lab.dev.lockpins (installed git commits against the checkout uv.lock)',
        'family_freshness': 'dev_lab.dev.lockpins.lock_freshness',
        'family-heads.json': 'nothing: the lock check is local and caches no forge head',
    }),
    **classified(
        "A session branch is protected for what it IS -- never by a directory or a host; its one writer is git's "
        'own one-checkout-per-branch and fast-forward rules (user rulings 2026-10-13/14; '
        'dev_lab.dev.sessionbranch).', {
        'INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT': 'ONE-WRITER-PER-SESSION-BRANCH and INTEGRATION-IS-MERGES-ONLY',
        'PUSH-FROM-ITS-OWN-BRANCH': 'PUBLISH-IS-THE-LOCAL-REF (dev_lab.dev.sessionbranch, at pre-push)',
        'integrationhome': 'dev_lab.dev.sessionbranch (pre-push) and dev_lab.dev.integrate (no working tree)',
        'integration-home': 'the base pre-push row `session-branch` (dev_lab/dev/githooks/base.toml)',
        'push-from-its-own-branch': 'the base pre-push row `session-branch` (dev_lab/dev/githooks/base.toml)',
        'integrator.host': 'nothing: no host owns integration; any host raises its prompts as one forge issue per row',
    }),
    **classified(
        "The integration branch moves by the FORGE's merge of a green PR; a client verb is a second writer (user "
        'ruling 2026-10-19, INTEGRATION-TAKES-ONLY-FORGE-MERGES).', {
        'integrate merge': 'the forge merges a green pull request server side (dev_lab.dev.forgemerge)',
        'forge promote': 'python -m dev_lab.dev.integrate promote <sha>, then a plain push of the trunk',
        'forgepromote': 'dev_lab.dev.integrate',
    }),
    **classified(
        'All maintenance happens on gitea; GitHub is ONLY a push mirror (user ruling 2026-10-19): nothing reads, '
        'carries, comments on or closes a GitHub PR, and auto-merge is gitea-only.', {
        'forgeintegration import venues': 'nothing: PRs are opened and merged on gitea; GitHub is only a push mirror',
        'forgemirror': 'nothing: GitHub is only a push mirror; open the PR on gitea (dev_lab.dev.forgemerge)',
        'GITHUB-PRS-ARRIVE-ON-THE-FORGE': 'nothing: GitHub is only a push mirror; PRs are opened on gitea',
        'integrator.mirror': 'nothing: GitHub is only a push mirror (gitea Settings > Mirror settings, branch-set.md)',
        'Marker.MIRROR': 'nothing: no comment is written on a GitHub PR; GitHub is only a push mirror',
        'mirror_step': 'nothing: forge serve and forge integration read gitea PRs only; GitHub is only a push mirror',
        'pr/gh-': 'pr/<name> on gitea (the declared ephemeral namespace); GitHub is only a push mirror',
    }, lost='capability-dropped'),
    **classified(
        'Files are an HPC resource: retention runs every serve tick, and a tree goes once its sha is not in '
        'flight.', {
        'every_hours': 'nothing: retention runs every serve tick (dev_lab.dev.retention)',
        'tree-unreferenced': 'tree-not-in-flight (dev_lab.dev.retentionplan.in_flight)',
    }),
    **classified(
        'Outcome is the task-result word lab_commons.outcomes owns (plan A1.8, 2026-10-18).', {
        'retention.Outcome': 'dev_lab.dev.retention.Freed',
        'box.Outcome': 'lab_commons.box.ProcessEnd (its .status maps a kill onto lab_commons.outcomes.Status)',
        'deploy.Outcome': 'dev_lab.supervise.deploy.PhaseResult (its .status reads OK or ERROR)',
        'execute.Outcome': 'lab_commons.compute.execute.Settled',
        'tools.install.Outcome': 'lab_commons.tools.install.ToolReport',
        'verdict.Outcome': 'lab_commons.outcomes.Conclusion (same tokens: pass, fail, inconclusive)',
        'forgestatus.Status': 'dev_lab.dev.forgestatus.CommitStatus (a forge commit-status record, not a task result)',
        'forgestatus import Status': 'from dev_lab.dev.forgestatus import CommitStatus',
    }),
    **classified(
        'A round streams into ONE file, kept as one file per run (HPC one-file plan, phase 5, 2026-10-18).', {
        'pack_streams': 'lab_commons.hpc.records.kept_text (the round file, its retired shape upgraded on read)',
        'KEPT_STREAMS': 'lab_commons.hpc.records.stream_sources (``runs/<sha>/streams.jsonl``)',
    }),
    **classified(
        'A request is a job on the part path: admitted by its declared need-set and run (lab-commons 2026-10-13).', {
        'claims requests yet': 'dev_lab.dev.forgerequest (admitted by its declared need-set, claimed and run)',
    }),
    **classified(
        'Nobody runs the gate to push: the forge produces the verdict once per sha (rule rows, 2026-10-11).', {
        'ONE-RUN-AFTER-INTEGRATION': (
            'VERDICTS-FROM-THE-FORGE (the forge serve producer runs the full suite, once per sha)'
        ),
        'SUBAGENT-NO-HEAVY-NO-PUSH': 'SUBAGENT-NO-PUSH (a full tier refuses everyone outside the producer)',
        'SUBAGENT_NO_HEAVY_NO_PUSH': 'dev_lab.dev._deny_subagent_row.SUBAGENT_NO_PUSH',
        'lab_commons.hpc verdict': 'python -m lab_commons.hpc run submit|gather (a run, not a verdict)',
        'lab_commons.hpc.verdict': 'lab_commons.hpc.run',
        'submit_verdict': 'lab_commons.hpc.run.submit_run',
        'gather_verdict': 'lab_commons.hpc.gather.gather_run',
        'VerdictSpec': 'lab_commons.hpc.shell.RunSpec',
        'record-linux': 'python -m dev_lab.dev.platformparts post <run-record> (platform and scope come from it)',
        'platformparts record': (
            'python -m dev_lab.dev.platformparts post <run-record> (a part is a tests/<part> status on the forge, '
            'not a ledger row)'
        ),
        'run_part': 'dev_lab.dev.platformparts.part_of (a record proves a Part, posted as a status)',
        'parts_for': 'dev_lab.dev.platformparts.parts_of (the parts of a commit, read from its forge statuses)',
        'record_gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
        '--trunk-tier': 'nothing: the trunk admits parts of scope full read from the forge (dev_lab.dev.pushgate)',
        'pushgate --ledger': 'nothing: the push gate reads the forge, never the local ledger',
        'pushgate --gap': 'nothing: a failing part is a failure status on the forge, linked to its run record',
    }),
    **classified(
        "A merge's deviations are checked when it is committed; a push does no audit work.", {
        'unpublished_merges': 'nothing: python -m dev_lab.dev.mergeaudit <msg-file> audits the merge being committed',
        'merge_refusals': 'dev_lab.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
        'mergeaudit.refusals': 'dev_lab.dev.mergeaudit.pending (the commit-msg hook, on the one merge being committed)',
        'run submit --history': (
            "nothing: run submit plans from the repository's last record (lab_commons.compute.learned)"
        ),
    }),
    **classified(
        'A cluster part is pulled by the cluster and advanced one step per serve tick, never a blocking process.', {
        'platformparts cluster': 'forge serve on the cluster login node (dev_lab.dev.forgerun advances the run)',
        '<job>.run.json': '<job>.submission.json (the last submission of a job file)',
    }),
    **classified(
        'One name for a platform result: a tests/<part> part, read through platformparts.standing.', {
        'lab/gate': 'tests/<part> (a part, dev_lab.dev.platformparts)',
        'lab/heavy': 'tests/<part> parts of scope full (what the trunk admits)',
        'GATE_CONTEXT': 'dev_lab.dev.platformparts.part_context(platform)',
        'HEAVY_CONTEXT': 'dev_lab.dev.platformparts.part_context(platform)',
        'gate_context': 'nothing: the queue reads tests/<part> parts, not a declared context',
        'heavy_context': 'nothing: the queue reads tests/<part> parts, not a declared context',
        'gate_ready': 'dev_lab.dev.integrator.lane_ready',
        'HEAVY_NEEDED': 'dev_lab.dev.integrator.PARTS_NEEDED',
        'heavy-needed': 'parts-needed',
        'publish_verdict': 'dev_lab.dev.verify.post_part (a settled run posts its platform part)',
    }),
    **classified(
        'The local verdict ledger was only a runner cache: it is the run cache now, and decides nothing.', {
        'verdictledger': 'dev_lab.dev.runcache (the cache) and dev_lab.dev.test_outcomes (the pytest plugin)',
        'record_promoted': 'dev_lab.dev.runcache.remember',
        'LEDGER_REL': 'dev_lab.dev.runcache.CACHE_REL',
        'output/verdicts/ledger.jsonl': 'output/runcache/record.jsonl',
    }),
    **classified(
        'The run cache is an outcomes Record in its own file (plan A1.5): a retired-code checkout keeps its old '
        'file.', {
        'runcache/runs.jsonl': 'output/runcache/record.jsonl (dev_lab.dev.runcache.CACHE_REL)',
    }),
    **classified(
        "How a cluster env is built is the commit's own declaration, read at --sha.", {
        'run submit --install': "the commit's [tool.lab_commons.hpc] install, read at --sha",
        'run submit --needs': "the commit's [tool.lab_commons.hpc] needs, read at --sha",
    }),
    **classified(
        'Git hooks are TOML run by one runner in the repo venv (user ruling 2026-10-11): no pre-commit framework.', {
        '.pre-commit-config.yaml': (
            '[[tool.dev_lab.hooks.<stage>]] in pyproject.toml over dev_lab/dev/githooks/base.toml'
        ),
        'PRE_COMMIT_REMOTE_BRANCH': "git's pre-push lines on stdin (dev_lab.dev.githooks.runner.parse_push_lines)",
        'PRE_COMMIT_FROM_REF': "the remote sha of git's pre-push lines on stdin",
        'PRE_COMMIT_TO_REF': "the local sha of git's pre-push lines on stdin",
        'PRECOMMIT_BASE': 'dev_lab/dev/githooks/base.toml (the hook base, as TOML data)',
        'HOOK_ID_CORE': 'dev_lab.dev.githooks.table.base_rows (the hook base, as TOML data)',
        'hook_requirement': 'nothing: every hook runs in the repo venv, so no second lab-commons is installed',
        'pre_commit install': 'python -m dev_lab.dev.hook_install --install',
        'install_command': 'dev_lab.dev.hook_install.INSTALL_COMMAND',
        'lab-with-venv': 'a hook row run = ["python", ...] (the runner already runs in the repo venv)',
        'lab-issue-ref': 'python -m dev_lab.dev.issueref (the base commit-msg row)',
        'githooks.bootstrap': 'dev_lab.dev.hook_install.shim (finds the checkout venv, execs the runner)',
        'branch-push-only': 'a hook row`s refs regex (pre-push: run only for matching pushed refs)',
        'lab_commons.dev.githooks bump-version': 'python -m dev_lab.dev.githooks script bump-version',
    }),
    **classified(
        'The base pre-push row runs the check in the repo venv; a shell wrapper hunting an interpreter is '
        'baggage.', {
        'lab_commons.dev.githooks branchset-push': _BRANCHSET_ROW,
        'githooks script branchset-push': _BRANCHSET_ROW,
        'branchset-push.sh': _BRANCHSET_ROW,
        'lab_commons.dev.githooks cz-push-range': 'python -m dev_lab.dev.githooks script cz-push-range',
    }),
    **classified(
        'A session share is temporary: derived from Slurm or one expiring forge record, never a machine file '
        '(2026-10-17).', {
        '[hpc.session.': (
            'python -m dev_lab.dev.hpcaccount allocate (one expiring forge record), else the derived share'
        ),
    }),
    **classified(
        'The verbs that launch the forge moved to dev-lab with it (dev-lab cut-over, 2026-10-22).', {
        'lab_commons.hpc deploy': 'python -m dev_lab.dev.hpcaccount deploy (a forge serve host is the forge`s)',
        'lab_commons.hpc tick': 'python -m dev_lab.dev.hpcaccount tick (the cron line the dev-lab deploy writes)',
        'lab_commons.hpc allocate': 'python -m dev_lab.dev.hpcaccount allocate (the record lives on the forge)',
    }),
    **classified(
        "A remote door is named for its target's kind (user ruling 2026-11-07): a cluster account or a server.", {
        'dev_lab.dev.hpchost': 'dev_lab.dev.hpcaccount (a cluster account); a server is dev_lab.dev.opsserver',
    }),
    **classified(
        'Deny-row IDs renamed, read by dev_lab.dev._deny_rows.retired_row under the key `deny row <ID>`.', {
        'deny row GIT-NETWORK-VERB': 'NETWORK-VERB',
        'deny row REVIEW-HOLD-VIA-TAKEOVER': 'STATUS-VIA-DOORS',
        'deny row REVIEW-STATUS-VIA-DOORS': 'STATUS-VIA-DOORS',
        'deny row CONSENT-CODE-NEVER-TYPED': 'CONSENT-CODE-FROM-THE-USER',  # 2026-11-08 user ruling
    }),
    **classified(
        'Where a run is and what the cluster can take are read from the forge, from any machine; no verb asks '
        'over ssh.', {
        'lab_commons.hpc probe': (
            'python -m dev_lab.dev.forge queue (each cluster host card carries its supply, partitions and quota)'
        ),
        'lab_commons.hpc run status': 'python -m dev_lab.dev.forge queue (each run in flight shows its progress)',
        'lab_commons.hpc run watch': (
            'forge serve on the cluster login node (it gathers each run it owns, a step a tick)'
        ),
    }),
    **classified(
        'A local verify posts no part: only the producer forge serve spawned does (VERDICTS-FROM-THE-FORGE, '
        '2026-10-18).', {
        '--no-status': 'nothing: a local verify never posts; only the producer forge serve spawned posts its part',
        'card.delegates': 'dev_lab.dev.forgehosts.card_owners (a card is vouched for by its forge-recorded author)',
    }),
    **classified(
        'A tool is present when found, at the declared version, smoke-tested and not failed since (D21, '
        '2026-10-16).', {
        'TOOL_PROBES': 'lab_commons.compute.toolchecks.CHECKS (find, version and smoke per tool)',
        'femm_found': 'lab_commons.compute.toolchecks.femm_find (one step of the femm check)',
        'matlab_found': 'lab_commons.compute.toolchecks.matlab_find (one step of the matlab check)',
        'simba_found': 'lab_commons.compute.toolchecks.simba_find (one step of the simba check)',
        'card.provides': 'dev_lab.dev.forgehosts.HostCard.tools (each tool as probed, with its evidence)',
    }),
    **classified(
        'Issues never rot (user ruling 2026-10-15): every tidy rule is on, forge serve runs it, a kind is a '
        'Marker.', {
        'forgetidy.SUBJECT': 'dev_lab.dev.forgepulls.Marker.TIDY (every marker kind is a Marker member)',
        'forgeintegration.SUBJECT': 'dev_lab.dev.forgepulls.Marker.INTEGRATION (every marker kind is a Marker member)',
        'hygiene.any': 'nothing: every forge tidy rule is on; a repo only tunes [tool.dev_lab.forge.issue_hygiene]',
        'run by `forge integration`': 'forge serve runs forge tidy on every host every tick (dev_lab.dev.forgeserve)',
    }, lost='capability-dropped'),
    **classified(
        'A lock-consuming door is judged against the COMMITTED lock: the old name described the untracked era.', {
        'REVERTS': 'UNLOCKED (dev_lab.dev.installdoor.Delivery.UNLOCKED)',
        'RevertingDoorsError': 'dev_lab.dev.installdoor.UnlockedDoorsError',
        'reverting(': 'dev_lab.dev.installdoor.unlocked(',
    }),
    **classified(
        'PIN + FRESHNESS (user ruling 2026-10-17): the lock is committed and the developer who pushes keeps it at'
        ' the newest resolution (dev_lab.dev.lockcheck). No lock AGE, no scheduled re-lock, no tip folded into a '
        'key.', {
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
    }, lost='capability-dropped'),
    **classified(
        'An HPC job ships ONE file and leaves a few (plan 2026-10-17): one artifact per sha, one results file per'
        ' run.', {
        'files_needed': 'lab_commons.hpc.plan.FILES_PER_RUN (one results file per run: its width adds no file)',
        'FILES_PER_SHARD': 'lab_commons.hpc.plan.FILES_PER_RUN (one results file per run: its width adds no file)',
        'occupied_trees': 'dev_lab.dev.retentionplan.occupied_shas (a test run tree or a job run directory)',
        '--output=logs/': 'nothing: a task writes to node-local $TMPDIR; its tail is kept in results.jsonl',
        'reuse that tree and env': 'run build builds the commit artifact, one file (lab_commons.hpc.artifact)',
    }),
    **classified(
        "A test's result is an outcomes Cell under its Expectation (plan A1.1/A1.2, user rulings 2026-10-18).", {
        'not-covered': "nothing: a handed id is no cell; the run record's `handed` names its part",
        'NOT_COVERED': "nothing: a handed id is no cell; the run record's `handed` names its part",
        'UNCARRIED': 'nothing: lab_commons.hpc.records.held_cells offers DECIDED cells only',
        'held_outcomes': 'lab_commons.hpc.records.held_cells',
    }),
    **classified(
        'A run state migrates ONCE (FORMATS-MIGRATE-ONCE, D51 2026-11-11); its reader refuses a retired shape. A '
        'persisted format is never upgraded on read (user ruling D51, 2026-11-11): it migrates once, then '
        'refuses.', {
        'FORMATS-UPGRADE-ON-READ': 'FORMATS-MIGRATE-ONCE (tests/test_arch_formats_migrate_once.py)',
        'test_arch_formats_upgrade_on_read': 'tests/test_arch_formats_migrate_once.py',
        'upgraded_state': 'lab_commons.hpc.records.current_state (reads), migrated_state (the one-shot migration)',
        'lab_commons.dev.outcomes': 'dev_lab.dev.test_outcomes (a Record of test cells per process)',
        'read_reasons': 'dev_lab.dev.test_outcomes.read_cells (a reason travels in its cell)',
        'OutcomeRecorder': 'dev_lab.dev.test_outcomes.CellRecorder',
        'outcome_of(': 'lab_commons.hpc.pytest_item.judged (the one pytest mapping, to a plain test cell)',
    }),
    **classified(
        'One object-store client for the family, product code included (D112 F1, 2026-11-25): it moved here '
        'whole.', {
        'dev_lab.dev.largeobjects': (
            'lab_commons.objects (RcloneObjects, declared_objects); '
            'the reach-proving door is dev_lab.dev.storage.run_objects'
        ),
    }),
    **classified(
        "The rendered rclone config's place is a fact a product reads too (D112 F1): it lives in "
        'lab_commons.objects.', {
        'DEFAULT_RCLONE': 'lab_commons.objects.RCLONE_CONFIG',
    }),
    **classified(
        'The tables dev-lab alone reads moved to [tool.dev_lab] with no fallback (plan A3 2026-10-27).', {
        **{
            f'[tool.lab_commons.{table}]': f'[tool.dev_lab.{table}] (plan A3: a table only dev-lab reads)'
            for table in MOVED_TABLES
        },
    }),
}
# fmt: on

#: Retired spelling -> its replacement: what every refusal names.
RETIRED: Final = {spelling: row[0] for spelling, row in CLASSIFIED.items()}

#: THE RETIRED PER-TEST WORDS, and only for LIVE state -- a cluster run's ``state.json`` and the stream of a
#: round in flight across the change (OLD-SHAPE-READER): each word -> ``(status word, reason kind)`` it
#: now reads as (:func:`lab_commons.hpc.testcells.upgraded`; a status word is ``TestCell.word``'s spelling).
#: ``None``: a handed id, which is no cell. A regenerable artifact in these words is refused, never upgraded.
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


#: :data:`RETIRED` as the family mechanism's registry: one matcher, one refusal sentence.
REGISTRY: Final = Registry(Retired(spelling, remedy) for spelling, remedy in RETIRED.items())


def retired_in(text: str) -> list[str]:
    """Every retired spelling *text* carries, in registry order (flags as whole tokens)."""
    return [row.name for row in REGISTRY.found_in(text)]


def refusal(prog: str, word: str) -> str | None:
    """The refusal for *word* used as a verb of *prog*, naming its replacement; ``None`` when it is not retired."""
    row = REGISTRY.get(f'{prog} {word}') or REGISTRY.get(word)
    return None if row is None else Retired(f'{prog} {word}', row.replacement).door()


def refuse_retired_keys(table: str, keys: Iterable[str]) -> None:
    """Refuse every key of machine-file ``[table]`` that is a retired spelling, naming each remedy."""
    found = [f'[{table}] {key} is retired: {RETIRED[key]}' for key in keys if key in RETIRED]
    if found:
        msg = 'the machine file: ' + '; '.join(found)
        raise ValueError(msg)
