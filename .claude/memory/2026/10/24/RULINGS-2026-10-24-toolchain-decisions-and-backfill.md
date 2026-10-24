---
created: 2026-10-24
type: decision
---

# User rulings 2026-10-24: every open toolchain decision, and backfill over strict FIFO

Context: `HANDOFF-integrator-toolchain-refactor.md` (same day) section 4. Principles applied: first principles, no
historical baggage, Occam, one source of truth, layered decoupling, names match, no backward compatibility.

## Rulings (all user-approved)

1. Reinstall motronics' shared `.git/hooks` from dev-lab NOW, not at #4802 landing.
2. Lock freshness and branchset leave the local pre-push entirely and run on the forge only; auto-sync relocks.
   No "not behind integrate/main" half-gate kept locally.
3. Dependency pins: `test_no_source_claims_a_dependency_pin` wins. uv.lock is the one version fact; pyproject
   carries floors only; the `_REV` comment allowing `@sha` goes; Cargo's numerics rev follows uv.lock.
4. `reference = "vendor=jmag"` in the 20 `validation.toml` files is deleted: `hamilton/arbiter.py::arbiters_for`
   decides the reference by reach, so a case never names its arbiter. Verify none deviates from the ladder first.
5. Stage memory reservation: PORT the deadline-aware backfill (branch `pr/a-stage-may-reserve-memory-without-cores`,
   fcadafd `box/order.py`), REPLACING integrate/main's FIFO-with-reservation. One admission rule, no switch.
   Evidence: 2026-10-23, three 1.74 GB JMAG reads idled hours behind an unfittable 6.45 GB run.
   Rule: a later ticket that fits is admitted iff it delays no earlier contending ticket's earliest start (from
   holders' deadlines, which the ledger enforces). The 7 tests pinning strict FIFO are rewritten; add a planted
   control: a small ticket that WOULD delay the head is refused.
6. The handoff and decision log land through a lab-commons `pr/*` branch.
7. `[tool.dev_lab.*]` rename one cut, no read-time upgrade; rendered family files not committed (bootstrap renders,
   famfiles pre-push goes); an over-cap PR waits for sliced review rather than a raised cap.

## Paging is not backfill

`hpc/grants.py` paging (user design 2026-10-17) prevents FRAGMENTATION of the cluster pool (page-sized tasks, a
large-job region). The local box ledger holds one fungible memory number, so it has no holes; its defect was
head-of-line ORDER. The two solve different problems; paging stays HPC-only.

## Found while checking

P2 is less done than the handoff said: `box`, `compute`, `hpc`, `supervise` are still in lab-commons
(`src/lab_commons/`); dev-lab holds only `dev/`. Backfill lands where box lives today.

## Done this session

- dev-lab #35 c12a2ad: stray-hook test resolves bash via `githooks.bash_executable` (serve host PATH has no bash).
- dev-lab `pr/stoprun-proves-an-orphaned-worker` c714e9d: orphaned hpchost PLC0415 waiver removed.
