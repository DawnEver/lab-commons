---
name: one-resource-path-p1-occupancy-through-the-engine
description: P1 of DESIGN-one-resource-path -- box OS, tool seats, lent slots and Slurm accounts are Providers; every admission calls resources.admits; the box ledger schema only grows, after a column rename broke every older process on the box.
metadata:
  type: project
created: 2026-10-26
accessed: 2026-10-26
---

# One resource path, P1: occupancy through the engine

- ONE PACKAGE `lab_commons.resources` (engine, model, dimensions); the old `resource` + `resources.py` are folded.
- THE RULE has one caller: `resources.engine.admits` (a free vector, present names, needs, running). The set of
  functions building a `Supply` or calling `admit` is pinned by name (`tests/test_arch_one_resource_path.py`;
  dev-lab's pin is the EMPTY set).
- PROVIDERS measure their scope LESS WHAT IS USED OUTSIDE THE LEDGER; the engine subtracts what the ledger holds.
  `box.providers`: `BoxOS`, `ToolSeats` (the one seat reader is `seat_sizes`), `Lent`, `measured`/`size` (the box
  measure on a reading taken before the lock), `box_engine`, `account_seats`. `hpc.provider`: `SlurmAccount`,
  `unmeasured`. No margin or share is held back for people (ruling 2026-10-26); `[reserve]` OS headroom stays,
  question open with the user.
- DELETED: `STATED_ONLY`, `compute.model.submitted` (a cluster demand declares `submits: 1`),
  `compute.admission.claimable`, `box.declared_seats`/`seat_supply`, `compute.probe.box/account/seated/tools/
  inventory` (`compute.probe` keeps only the OS names), inline `Supply(`/`admit(` in ledger/order and in dev-lab's
  forgetake/forgetry/forgetick/forgeslurm. A cluster tick with no measured supply raises.
- EXTERNAL JOB API: `box.external(demand, wall_s, what, *, key, probe, lent=...)` -- a JMAG Job System job on this
  node held at `cpu=cores_per_job` + seat + `jobslot:<node>`, persisted with a liveness probe; `hand_over` to the
  solver pid attributes its measured use.

## The incident: a schema rename on the shared ledger

The branch first renamed the ledger column `seats` -> `amounts` on open. The verify door's own claim sits on the
REAL ledger (`%ProgramData%/lab-commons/box.sqlite`) and ran the branch's code, so the machine file was renamed and
every process on the published lab-commons failed `no such column: seats`. The coordinator restored it (backup
`box.sqlite.bak-20261026`). Ruling: the rename is dropped -- `seats` keeps its name and holds every non-cpu/mem
amount by name -- and `tests/test_box_store_is_additive.py` refuses any ledger DDL but a guarded CREATE or an
ADD COLUMN an older writer may omit. That is what makes the verify door safe on any tree: the file it opens stays
readable and writable by the published version. Each test opens its own ledger (`conftest._a_box_of_its_own`).
