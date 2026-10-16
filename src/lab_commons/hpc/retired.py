"""RETIRED SPELLINGS of the dev and test-run surface, each with its replacement -- the one place they may appear.

The noun that named three things (forge plan P1, user ruling 2026-10-09) was split into a RUN (one
execution of a test set on one platform at one sha; its record is ``run-<sha>-<part>-<req>.json.gz``),
a PART (a platform's result for a sha, composed of runs) and an ADMISSION (computed, never stored).
No alias survives: a command line still using a retired word is REFUSED BY NAME with the remedy
(:func:`refusal`), and ``tests/test_hpc_retired.py`` reds on any retired spelling anywhere else in
the tracked tree, prose included.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

__all__ = ['RETIRED', 'refusal', 'refuse_retired_keys']

_BRANCHSET_ROW: Final = 'the base pre-push row `branchset-push` (lab_commons/dev/githooks/base.toml)'

#: Retired spelling -> its replacement. Keys are matched as plain substrings of tracked text.
RETIRED: Final[dict[str, str]] = {
    # A session branch is protected for what it IS -- never by a directory or a host; its one writer is git's own
    # one-checkout-per-branch and fast-forward rules (user rulings 2026-10-13/14; lab_commons.dev.sessionbranch).
    'INTEGRATION-LIVES-IN-THE-MAIN-CHECKOUT': 'ONE-WRITER-PER-SESSION-BRANCH and INTEGRATION-IS-MERGES-ONLY',
    'PUSH-FROM-ITS-OWN-BRANCH': 'PUBLISH-IS-THE-LOCAL-REF (lab_commons.dev.sessionbranch, at pre-push)',
    'integrationhome': 'lab_commons.dev.sessionbranch (pre-push) and lab_commons.dev.integrate (no working tree)',
    'integration-home': 'the base pre-push row `session-branch` (lab_commons/dev/githooks/base.toml)',
    'push-from-its-own-branch': 'the base pre-push row `session-branch` (lab_commons/dev/githooks/base.toml)',
    'integrator.host': 'nothing: no host owns integration; any host raises its prompts as one forge issue per row',
    'forge promote': 'python -m lab_commons.dev.integrate promote <sha>, then a plain push of the trunk',
    'forgepromote': 'lab_commons.dev.integrate',
    # Files are an HPC resource: retention runs every serve tick, and a tree goes once its sha is not in flight.
    'every_hours': 'nothing: retention runs every serve tick (lab_commons.dev.retention)',
    'tree-unreferenced': 'tree-not-in-flight (lab_commons.dev.retentionplan.in_flight)',
    # A request is a job on the part path: admitted by its declared need-set and run (lab-commons 2026-10-13).
    'claims requests yet': 'lab_commons.dev.forgerequest (admitted by its declared need-set, claimed and run)',
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
        'python -m lab_commons.dev.platformparts post <run-record> (a part is a tests/<part> status on the forge, '
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
    'run submit --history': "nothing: run submit plans from the repository's last record (lab_commons.compute.learned)",
    # A cluster part is pulled by the cluster and advanced one step per serve tick, never a blocking process.
    'platformparts cluster': 'forge serve on the cluster login node (lab_commons.dev.forgerun advances the run)',
    '<job>.run.json': '<job>.submission.json (the last submission of a job file)',
    # The machine's box ledger and an environment's mutation seat are two resources with two names.
    'BoxLock': 'lab_commons.box.run (every run declares its claim on the one box ledger and is held to it)',
    'boxlock': 'lab_commons.box',
    'BOX_POOL': 'lab_commons.box (one ledger per machine, lab_commons.box.box_path)',
    'EnvLock': 'lab_commons.dev.envseat.EnvironmentSeat (keyed by one environment prefix)',
    'envlock': 'lab_commons.dev.envseat',
    'hold_the_box': 'lab_commons.box.run (the claim), and lab_commons.dev.envseat.EnvironmentSeat (the env)',
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
    # The base pre-push row runs the check in the repo venv; a shell wrapper hunting an interpreter is baggage.
    'lab_commons.dev.githooks branchset-push': _BRANCHSET_ROW,
    'githooks script branchset-push': _BRANCHSET_ROW,
    'branchset-push.sh': _BRANCHSET_ROW,
    'lab_commons.dev.githooks cz-push-range': 'python -m lab_commons.dev.githooks script cz-push-range',
    # Where a run is and what the cluster can take are read from the forge, from any machine; no verb asks over ssh.
    'lab_commons.hpc probe': (
        'python -m lab_commons.dev.forge queue (each cluster host card carries its supply, partitions and quota)'
    ),
    'lab_commons.hpc run status': 'python -m lab_commons.dev.forge queue (each run in flight shows its progress)',
    'lab_commons.hpc run watch': 'forge serve on the cluster login node (it gathers each run it owns, a step a tick)',
    # What a host or account can take is its measured Supply (design: one admission, migration step 2, 2026-10-13).
    # The type is matched by its uses, not the bare word, which the ratchet prose uses in its plain sense.
    'Headroom(': 'lab_commons.compute.model.Supply (from lab_commons.compute.probe)',
    ': Headroom': 'lab_commons.compute.model.Supply',
    ', Headroom': 'lab_commons.compute.model.Supply',
    'box_headroom': 'lab_commons.compute.probe.box (free cores, memory, OS and tools measured now)',
    'no free core': "the admission's lacking resource: 'not admitted: lacking cpu' (compute.admission.admit)",
    # A tick claims in rank order WHILE JOBS ADMIT, several per host (design: one admission, step 6, 2026-10-13).
    'OUTRANKED': (
        'nothing: a tick claims every job that admits, in rank order (lab_commons.dev.forgetick); one that does not '
        "fit waits 'not admitted: lacking <resource>'"
    ),
    'outranked by': "'not admitted: lacking <resource>' (a higher-ranked claim is running demand, never a blocker)",
    'a run in flight here': 'nothing: a run in flight is running demand in the admission (lab_commons.dev.forgeserve)',
    'NO_HEADROOM': 'lab_commons.dev.forgeschedule.NOT_ADMITTED',
    'no headroom:': "'not admitted: lacking <resource>' (lab_commons.compute.admission.Admission.why)",
    # The host card's measured vector is its supply, read by that key only.
    'card.headroom': 'HostCard.supply (what the tick admitted against)',
    '"headroom"': 'the host card key "supply"',
    'headroom=dict(': 'HostCard(supply=...)',
    'carries its headroom': 'carries its supply',
    # One measured demand table for every host: per test and per (producer, need-set) job (compute step 4).
    'lab_commons.hpc.learned': 'lab_commons.compute.learned (the one measured demand table)',
    'lab_commons.hpc.measured': 'lab_commons.compute.learned.Measured',
    'hpc-learned': 'compute-learned (beside the machine config, lab_commons.compute.learned.directory)',
    'lab_commons.hpc.outstanding': 'lab_commons.dev.forgerun (the runs in flight, one state file each, in forge-runs/)',
    # A host's own user is served by its own reserve, and an account is divided by reservations (2026-10-13).
    'own_weight': (
        'nothing: the own reserve (lab_commons.box.machine.own_reserve) keeps the [reserve] margins for the user '
        'of this box -- delete the key'
    ),
    '--own-weight': 'nothing: the box keeps its [reserve] margins for its own user; favour work with [priority]',
    '[[hpc.grant]] cpus': ('nothing: admission fits each job against what the account has free now -- delete the line'),
    # A box job's cpu was capped at the box's because its cpu was wall x cores; it is now measured (2026-10-13).
    'model.fitted': 'nothing: a demand is what the job measured (lab_commons.proccpu.TreeCpu); compute.model.submitted',
    # A carry is derived from each test's footprint, never declared per repo (2026-10-13).
    'inert_globs': 'lab_commons.dev.footprint.Footprints (the footprint of each test file, derived at the judged sha)',
    'parts] inert': (
        'nothing: what a change reaches is derived per test (lab_commons.dev.footprint); declare only what a '
        'module reads by path, [tool.lab_commons.carry.reads] -- delete the key'
    ),
    'grant.cpus': 'lab_commons.hpc.plan.headroom(snapshot) (a grant carries no share)',
    # One need vocabulary: a test marks the resources it needs, and its part is its need-set (compute step 3).
    '[tool.lab_commons.platforms]': (
        '[tool.lab_commons.tests] resources = [<marker>, ...] -- an OS is a resource like any other, and a test '
        'needing a tool marks the tool, never the OS the tool runs on (lab_commons.hpc.needsets)'
    ),
    '[tool.lab_commons.capabilities]': (
        '[tool.lab_commons.tests] resources = [<marker>, ...] -- every resource is equal; which host has one is '
        "its machine file's [capabilities.<name>] and the probes (lab_commons.compute.probe)"
    ),
    'engine = true': 'nothing: a resource marker is its own part only by being a need-set (tests/<part>)',
    'lab_commons.parts.produce': '[tool.lab_commons.tests] produce = [..., "{part}"] (the ONE producer of every part)',
    'produce.windows.full': '[tool.lab_commons.tests] produce = [..., "{part}"] (the ONE producer of every part)',
    'lab_commons.hpc.platforms': 'lab_commons.hpc.needsets (a part is a need-set, tests/<part>)',
    'cannot_run(': 'lab_commons.hpc.needsets.selecting(vocabulary, part) (exactly one need-set)',
    'CAPABILITY_ROWS': 'nothing: every resource is equal (lab_commons.hpc.needsets)',
    'served_parts': 'lab_commons.compute.admission.admit (needs present on the host, demand within its supply)',
    'declared_parts': 'lab_commons.dev.platformparts.derived (base, and every part a posted part hands to)',
    'part_argv': 'lab_commons.dev.platformparts.producer_argv (the one declared producer, {part} filled)',
    # Every host serves every candidate class from one queue (user ruling 2026-10-13, "fix all"): ORDER is
    # rank(), FIT is admit(), exclusivity is a reservation. A serve mode chose refs per host; none survives.
    '--mode': (
        'nothing: forge serve / forge tick / workstation deploy|serve take no mode -- every host serves every class '
        '(rank orders, admit fits); a host that takes no foreign work does not run forge serve'
    ),
    'forgetick.MODES': 'nothing: one queue for every host (lab_commons.dev.forgeschedule.candidates)',
    'forgetick import MODES': 'nothing: one queue for every host (lab_commons.dev.forgeschedule.candidates)',
    'develop|quiet': 'nothing: one queue for every host; a host that serves nobody else does not run forge serve',
    '[deploy] serve': 'nothing: delete the [deploy] serve key from the machine file (forge serve takes no mode)',
    'schedule] branches': (
        '[tool.lab_commons.schedule] classes_by_ref = {"<fnmatch>" = "<class>", ...} (ordered, first match; '
        'a ref no pattern matches is not served)'
    ),
    'lane_prefixes': (
        '[tool.lab_commons.schedule] classes_by_ref (a child branch is a ref the rule classes; '
        'lab_commons.compute.model.Schedule.class_of)'
    ),
    'forgealarm.REPEAT': "the machine file's [alerts] repeat_hours (lab_commons.dev.forgealarm.repeat_window)",
    'REPEAT: Final': "the machine file's [alerts] repeat_hours (lab_commons.dev.forgealarm.repeat_window)",
    'holds_against(': 'nothing: nothing is reserved (lab_commons.compute.admission.admit fits against what runs)',
    'compute.model.admit': 'lab_commons.compute.admission.admit (needs present, demand within supply less what runs)',
    'compute.model.Admission': 'lab_commons.compute.admission.Admission',
    # Reservations, leases, lending and preemption are DELETED (user ruling 2026-10-14, "A"): a box's supply is what
    # its one ledger can still admit (lab_commons.box), and priority is ordering only (lab_commons.compute.priority).
    'forgereserve': 'nothing: nothing is reserved; favour work with the machine file [priority] (ordering only)',
    'forge reserve': 'nothing: nothing is reserved; favour work with the machine file [priority] (ordering only)',
    'forge release': 'nothing: nothing is reserved, so there is no lease to release',
    'compute.ledger': 'lab_commons.dev.forgehosts.read_cards (the cards; no lease is stored)',
    'forgelend': 'nothing: nothing is lent; a box claim is enforced by the box (lab_commons.box.run)',
    'preempt_runs': 'nothing: nothing is preempted (lab_commons.compute.priority orders, it never displaces)',
    'PREEMPTED': 'lab_commons.dev.producer.RETRYABLE (the one release that leaves a part open)',
    'on_loan': 'nothing: a claim borrows nothing',
    'Reservation(': 'nothing: nothing is reserved (lab_commons.compute.admission.admit)',
    '[reserve] delegates': 'nothing: [reserve] keeps only the cpu_margin and mem_margin of the box',
    '--delegates': 'nothing: [reserve] keeps only the cpu_margin and mem_margin of the box',
    'card.delegates': 'lab_commons.dev.forgehosts.card_owners (a card is vouched for by its forge-recorded author)',
    # A tool is present when found, at the declared version, smoke-tested and not failed since (D21, 2026-10-16).
    'TOOL_PROBES': 'lab_commons.compute.presence.CHECKS (find, version and smoke per tool)',
    'femm_found': 'lab_commons.compute.presence.femm_find (one step of the femm check)',
    'matlab_found': 'lab_commons.compute.presence.matlab_find (one step of the matlab check)',
    'simba_found': 'lab_commons.compute.presence.simba_find (one step of the simba check)',
    'card.provides': 'lab_commons.dev.forgehosts.HostCard.tools (each tool as probed, with its evidence)',
    # Issues never rot (user ruling 2026-10-15): every tidy rule is on, forge serve runs it, a kind is a Marker.
    'forgetidy.SUBJECT': 'lab_commons.dev.forgepulls.Marker.TIDY (every marker kind is a Marker member)',
    'forgeintegration.SUBJECT': 'lab_commons.dev.forgepulls.Marker.INTEGRATION (every marker kind is a Marker member)',
    'hygiene.any': 'nothing: every forge tidy rule is on; a repo only tunes [tool.lab_commons.forge.issue_hygiene]',
    'run by `forge integration`': 'forge serve runs forge tidy on every host every tick (lab_commons.dev.forgeserve)',
    # A box host's supply is its one ledger (lab_commons.box): what it admits is what the box enforces.
    'learned_hold': 'lab_commons.compute.learned.box_demand (admitted at, and enforced at, one declared demand)',
    'learned_demand': 'lab_commons.compute.learned.cluster_demand, or box_demand for a box part',
    'box_reserve': 'lab_commons.box.Capacity.read (the box capacity after its [reserve] margins)',
    'seat_running': 'lab_commons.box.holdings (every claim on the box ledger, already in the supply)',
    'given_back': 'nothing: the supply is the box ledger, so no measured use is given back',
    # The CPU seat and its bounded wait are DELETED: every run claims the one box ledger (lab_commons.box, P3).
    'cpuseat': 'lab_commons.box (every run declares its claim on the one box ledger and is held to it)',
    'CpuSeat': 'lab_commons.box.run, or python -m lab_commons.box run --cpu --mem-gb --wall --queue-s --key --outcome',
    'CPU_POOL': 'lab_commons.box.box_path (the one ledger of this machine)',
    'LIGHT_WIDTH': 'lab_commons.dev.verifybox.SELECTION_CPU (a selection declares its cores to the box)',
    'LIGHT_MEMORY_BYTES': 'lab_commons.dev.verifybox.SELECTION_MEM_GB (a selection declares its memory)',
    'boxwait': 'lab_commons.box (admission waits at most queue_s, then refuses naming every holder)',
    'hold_run_seats': 'lab_commons.box.command (the run in the box) and lab_commons.dev.envseat.EnvironmentSeat',
    'boxseat': 'nothing: the box ledger is machine-wide by construction (lab_commons.box.box_path)',
    'PRODUCE_WALL_S': 'lab_commons.compute.learned.BOX_WALL_S (the wall the box enforces)',
    # The box's decision is a RECORD, never a sentinel exit code a child could also choose (SR-20261015-002).
    'lab_commons.box.REFUSED': 'lab_commons.box.read_ended (the --outcome record: a Refused when nothing ran)',
    'lab_commons.box.KILLED': 'lab_commons.box.read_ended (the --outcome record: an Outcome whose killed is true)',
    'free_cores(': 'lab_commons.compute.probe.box (capacity less the box ledger claims, never a busy reading)',
    'available_mem_gb': 'lab_commons.compute.probe.box (capacity less the box ledger claims, never a free reading)',
}


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
