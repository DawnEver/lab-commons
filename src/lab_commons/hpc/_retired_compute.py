"""RETIRED SPELLINGS of the compute surface -- admission, supply, demand and the one box ledger.

Half of :data:`lab_commons.hpc.retired.RETIRED`, split by subject: the dev and test-run surface stays in
:mod:`lab_commons.hpc.retired`, which merges this table into the one registry every refusal and the
tracked-tree scan read. These two files are the only places a retired spelling may appear.
"""

from __future__ import annotations

from typing import Final

__all__ = ['COMPUTE']

#: Retired spelling -> its replacement, for the compute surface. Keys are plain substrings of tracked text.
COMPUTE: Final[dict[str, str]] = {
    # The push gate decides a push; `admit` is the one fit rule (lab_commons.compute.admission), D2 2026-10-16.
    'lab_commons.dev.admission': 'dev_lab.dev.pushgate (decide, Decision)',
    'dev import admission': 'from dev_lab.dev import pushgate',
    # The machine's box ledger and an environment's mutation seat are two resources with two names.
    'BoxLock': 'lab_commons.box.run (every run declares its claim on the one box ledger and is held to it)',
    'boxlock': 'lab_commons.box',
    'BOX_POOL': 'lab_commons.box (one ledger per machine, lab_commons.box.box_path)',
    'EnvLock': 'dev_lab.dev.envseat.EnvironmentSeat (keyed by one environment prefix)',
    'envlock': 'dev_lab.dev.envseat',
    'hold_the_box': 'lab_commons.box.run (the claim), and dev_lab.dev.envseat.EnvironmentSeat (the env)',
    'refuse_if_locked': 'nothing: dep.mutate holds the TARGET environment seat and refuses naming its holder',
    'LOCK_UNDECLARED': 'nothing: who may block a change is the target environment seat, not a port answer',
    'lock_holders': 'dev_lab.dev.envseat.EnvironmentSeat.holders(prefix)',
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
    # What a host or account can take is its measured Supply (design: one admission, migration step 2, 2026-10-13).
    # The type is matched by its uses, not the bare word, which the ratchet prose uses in its plain sense.
    'Headroom(': 'lab_commons.compute.model.Supply (measured by a provider, lab_commons.box.providers)',
    ': Headroom': 'lab_commons.compute.model.Supply',
    ', Headroom': 'lab_commons.compute.model.Supply',
    'box_headroom': 'lab_commons.box.providers.box_engine(...).available (what lies outside the ledger now)',
    'no free core': "the admission's lacking resource: 'not admitted: lacking cpu' (compute.admission.admit)",
    # A tick claims in rank order WHILE JOBS ADMIT, several per host (design: one admission, step 6, 2026-10-13).
    'OUTRANKED': (
        'nothing: a tick claims every job that admits, in rank order (dev_lab.dev.forgetick); one that does not '
        "fit waits 'not admitted: lacking <resource>'"
    ),
    'outranked by': "'not admitted: lacking <resource>' (a higher-ranked claim is running demand, never a blocker)",
    'a run in flight here': 'nothing: a run in flight is running demand in the admission (dev_lab.dev.forgeserve)',
    'NO_HEADROOM': 'dev_lab.dev.forgeschedule.NOT_ADMITTED',
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
    'lab_commons.hpc.outstanding': 'dev_lab.dev.forgerun (the runs in flight, one state file each, in forge-runs/)',
    # A host's own user is served by its own reserve, and an account is divided by reservations (2026-10-13).
    'own_weight': (
        'nothing: the own reserve (lab_commons.box.machine.own_reserve) keeps the [reserve] margins for the user '
        'of this box -- delete the key'
    ),
    '--own-weight': 'nothing: the box keeps its [reserve] margins for its own user; favour work with [priority]',
    '[[hpc.grant]] cpus': ('nothing: admission fits each job against what the account has free now -- delete the line'),
    # A box job's cpu was capped at the box's because its cpu was wall x cores; it is now measured (2026-10-13).
    'model.fitted': 'nothing: a demand is what the job measured (lab_commons.proccpu.TreeCpu)',
    # A carry is derived from each test's footprint, never declared per repo (2026-10-13).
    'inert_globs': 'dev_lab.dev.footprint.Footprints (the footprint of each test file, derived at the judged sha)',
    'parts] inert': (
        'nothing: what a change reaches is derived per test (dev_lab.dev.footprint); declare only what a '
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
        "its machine file's [capabilities.<name>] and the seat provider (lab_commons.box.providers.ToolSeats)"
    ),
    # A resource is a host CAPABILITY boundary: the checkout, node and codex live on the same hosts, so they are
    # one resource, and three parts queued three times on one host (user directive 2026-10-19).
    'mark.dev_checkout': 'pytest.mark.workstation (dev_lab.dev.workstation.WORKSTATION)',
    'mark.node': 'pytest.mark.workstation (dev_lab.dev.workstation.WORKSTATION)',
    'mark.codex': 'pytest.mark.workstation (dev_lab.dev.workstation.WORKSTATION)',
    'capabilities.dev_checkout': '[capabilities.workstation] (dev_lab.dev.workstation.WORKSTATION)',
    'tests/dev_checkout': 'tests/workstation (the one part of the workstation resource)',
    'tests/codex': 'tests/workstation (the one part of the workstation resource)',
    'tests/node': 'tests/workstation (the one part of the workstation resource)',
    'dev_checkout+node': 'workstation (one resource, one part)',
    'DEV_CHECKOUT': 'dev_lab.dev.workstation.WORKSTATION',
    'engine = true': 'nothing: a resource marker is its own part only by being a need-set (tests/<part>)',
    'lab_commons.parts.produce': '[tool.lab_commons.tests] produce = [..., "{part}"] (the ONE producer of every part)',
    'produce.windows.full': '[tool.lab_commons.tests] produce = [..., "{part}"] (the ONE producer of every part)',
    'lab_commons.hpc.platforms': 'lab_commons.hpc.needsets (a part is a need-set, tests/<part>)',
    'cannot_run(': 'lab_commons.hpc.needsets.selecting(vocabulary, part) (exactly one need-set)',
    'CAPABILITY_ROWS': 'nothing: every resource is equal (lab_commons.hpc.needsets)',
    'served_parts': 'lab_commons.compute.admission.admit (needs present on the host, demand within its supply)',
    'declared_parts': 'dev_lab.dev.platformparts.derived (base, and every part a posted part hands to)',
    'part_argv': 'dev_lab.dev.platformparts.producer_argv (the one declared producer, {part} filled)',
    # Every host serves every candidate class from one queue (user ruling 2026-10-13, "fix all"): ORDER is
    # rank(), FIT is admit(), exclusivity is a reservation. A serve mode chose refs per host; none survives.
    '--mode': (
        'nothing: forge serve / forge tick / workstation deploy|serve take no mode -- every host serves every class '
        '(rank orders, admit fits); a host that takes no foreign work does not run forge serve'
    ),
    'forgetick.MODES': 'nothing: one queue for every host (dev_lab.dev.forgeschedule.candidates)',
    'forgetick import MODES': 'nothing: one queue for every host (dev_lab.dev.forgeschedule.candidates)',
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
    'forgealarm.REPEAT': "the machine file's [alerts] repeat_hours (dev_lab.dev.forgealarm.repeat_window)",
    'REPEAT: Final': "the machine file's [alerts] repeat_hours (dev_lab.dev.forgealarm.repeat_window)",
    'holds_against(': 'nothing: nothing is reserved (lab_commons.compute.admission.admit fits against what runs)',
    'compute.model.admit': 'lab_commons.compute.admission.admit (needs present, demand within supply less what runs)',
    'compute.model.Admission': 'lab_commons.compute.admission.Admission',
    # Reservations, leases, lending and preemption are DELETED (user ruling 2026-10-14, "A"): a box's supply is what
    # its one ledger can still admit (lab_commons.box), and priority is ordering only (lab_commons.compute.priority).
    'forgereserve': 'nothing: nothing is reserved; favour work with the machine file [priority] (ordering only)',
    'forge reserve': 'nothing: nothing is reserved; favour work with the machine file [priority] (ordering only)',
    'forge release': 'nothing: nothing is reserved, so there is no lease to release',
    'compute.ledger': 'dev_lab.dev.forgehosts.read_cards (the cards; no lease is stored)',
    'forgelend': 'nothing: nothing is lent; a box claim is enforced by the box (lab_commons.box.run)',
    'preempt_runs': 'nothing: nothing is preempted (lab_commons.compute.priority orders, it never displaces)',
    'PREEMPTED': 'dev_lab.dev.producer.RETRYABLE (the one release that leaves a part open)',
    'on_loan': 'nothing: a claim borrows nothing',
    'Reservation(': 'nothing: nothing is reserved (lab_commons.compute.admission.admit)',
    '[reserve] delegates': 'nothing: [reserve] keeps only the cpu_margin and mem_margin of the box',
    '--delegates': 'nothing: [reserve] keeps only the cpu_margin and mem_margin of the box',
    # A box host's supply is its one ledger (lab_commons.box): what it admits is what the box enforces.
    'learned_hold': 'lab_commons.compute.learned.box_demand (admitted at, and enforced at, one declared demand)',
    'learned_demand': 'lab_commons.compute.learned.cluster_demand, or box_demand for a box part',
    # An unmeasured box part is priced as its selection, never a repo-wide prior (2026-10-19: 6 GB for 1 test).
    'part_prior': 'lab_commons.compute.learned.selection_gb (the files of the part, at the ids it is handed)',
    'PART_PEAKS': 'lab_commons.compute.learned.PART_FILES (the files a part ran; their peaks are per file)',
    'box_reserve': 'lab_commons.box.Capacity.read (the box capacity after its [reserve] margins)',
    'seat_running': 'lab_commons.box.holdings (every claim on the box ledger, already in the supply)',
    'given_back': 'nothing: the supply is the box ledger, so no measured use is given back',
    # The CPU seat and its bounded wait are DELETED: every run claims the one box ledger (lab_commons.box, P3).
    'cpuseat': 'lab_commons.box (every run declares its claim on the one box ledger and is held to it)',
    'CpuSeat': 'lab_commons.box.run, or python -m lab_commons.box run --cpu --mem-gb --wall --queue-s --key --outcome',
    'CPU_POOL': 'lab_commons.box.box_path (the one ledger of this machine)',
    'LIGHT_WIDTH': 'dev_lab.dev.verifybox.SELECTION_CPU (a selection declares its cores to the box)',
    'LIGHT_MEMORY_BYTES': 'dev_lab.dev.verifybox.selection_mem_gb (a selection declares its measured memory)',
    # ONE box ledger (D2 2026-10-16): the per-box broker, its pools, records and refusal fold into lab_commons.box.
    'resources.Broker': 'lab_commons.box.hold (cores, memory and seats on the one ledger)',
    'Broker(': 'lab_commons.box.hold',
    'CapacityRegistry': 'nothing: a seat counts its [capabilities] declaration, else 1 (lab_commons.box.supply.room)',
    'Exhausted': 'lab_commons.box.Refused (one refusal, naming what is lacking and its holders)',
    'RESOURCE_DIR_ENV': 'lab_commons.box.BOX_ENV (the one ledger file)',
    'resource_dir()': 'lab_commons.box.box_path().parent (the ledger directory, outside every repo)',
    'environment_pool': 'dev_lab.dev.envseat.environment_seat (a seat on the box ledger)',
    'bounded.worker_width': 'lab_commons.box.hold on the measured room; a selection declares selection_mem_gb',
    'SELECTION_MEM_GB': 'dev_lab.dev.verifybox.selection_mem_gb (its files measured peaks, D4/D5)',
    'boxwait': 'lab_commons.box (admission waits at most queue_s, then refuses naming every holder)',
    'hold_run_seats': 'lab_commons.box.command (the run in the box) and dev_lab.dev.envseat.EnvironmentSeat',
    'boxseat': 'nothing: the box ledger is machine-wide by construction (lab_commons.box.box_path)',
    'PRODUCE_WALL_S': 'lab_commons.compute.learned.BOX_WALL_S (the wall the box enforces)',
    # The box's decision is a RECORD, never a sentinel exit code a child could also choose (SR-20261015-002).
    'lab_commons.box.REFUSED': 'lab_commons.box.read_ended (the --outcome record: a Refused when nothing ran)',
    'lab_commons.box.KILLED': 'lab_commons.box.read_ended (the --outcome record: a ProcessEnd whose killed is true)',
    'free_cores(': 'lab_commons.box.supply.room (the measured room, read by lab_commons.box.providers)',
    'available_mem_gb': 'lab_commons.box.supply.room (the measured room, read by lab_commons.box.providers)',
    # ONE fit rule (compute.admission.admit) decides on the MEASURED room; the ledger HOLDS a claim (D2, D3).
    'lab_commons.box.admit': 'lab_commons.box.hold (books a claim the one rule admits on the measured room)',
    'lab_commons.box.fits': 'lab_commons.resources.admits on lab_commons.box.providers.measured',
    'demand.fits': 'lab_commons.resources.admits on lab_commons.box.providers.measured',
    # ONE concurrency mechanism (user ruling 2026-10-18): an in-flight run holds a seat on the box ledger.
    'claim_path': 'dev_lab.dev.inflight.seat / leader_run (the leader holds a seat on lab_commons.box)',
    # ONE RESOURCE PATH P1 (2026-10-26): occupancy goes through the engine; one seat reader; no inline Supply.
    'STATED_ONLY': 'nothing: a name is metered where a provider on its scope supplies it (lab_commons.resources)',
    'compute.model import submitted': 'nothing: a cluster demand declares its submits (compute.learned.cluster_demand)',
    'seat_supply': 'lab_commons.box.providers.seat_sizes (the one seat reader)',
    'declared_seats': 'lab_commons.box.providers.ToolSeats (sizes from seat_sizes, presence from compute.presence)',
    'probe.seated': 'lab_commons.box.providers.ToolSeats',
    'probe import tools': 'lab_commons.box.providers.ToolSeats',
    'probe.box(': 'lab_commons.box.providers.box_engine(...).available',
    'probe import account': 'lab_commons.hpc.provider.SlurmAccount',
    'probe.account(': 'lab_commons.hpc.provider.SlurmAccount',
    'compute.admission import claimable': 'nothing: the forge claim pass walks its order through lab_commons.resources',
    'lab_commons.resource.': 'lab_commons.resources. (one package: the engine, its kinds and the dimensions)',
    'from lab_commons.resource import': 'from lab_commons.resources import',
}
