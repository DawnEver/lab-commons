---
created: 2026-10-22
accessed: 2026-10-22
---

# A consumer lends a named capacity to the box ledger (`Capacity.lent`)

- NEED (motronics, 2026-10-22): JMAG Job System node slots (`WS1 = 4, WS2 = 2`) must be shared by every
  sweep process on a machine, freed when a process dies, and counted once. motronics had grown a second
  ledger for them (an OS file-lock `SlotPool` beside `box.sqlite`). The box ledger already had everything but
  the capacity: seats were sized only by the machine file's `[capabilities]`.
- CHOSEN: `Capacity.lent({'<kind>:<instance>': n})` -- a capacity of lent resources alone (no cores, memory).
  The `:` is the point: `machine.RESOURCE_NAME` forbids it, so a lent count and a machine-file count can never
  name the same thing (one source by construction, not a runtime check). An unlent name still counts 1, like
  every undeclared seat: `inflight:<digest>` (dev.inflight) is an EXCLUSION spelled with a `:`, and reading
  `:` as 'lent, so 0 when unlent' looped verify forever on its own in-flight seat (caught 2026-10-22).
- A seats-only demand (cpu 0, mem 0) is decided on a zero `Reading`: no 0.25 s OS sample per poll.
- FIFO: a seats-only ticket contends only with tickets wanting the same name, so it cannot sit behind a
  memory waiter (the 2026-10-19 head-of-line deadlock needs a holder queueing for what a waiter ahead wants).
- TRUST: every claimant must lend the same count; the ledger decides on the caller's capacity. Callers read
  one table, so they agree; a disagreeing caller is admitted against its own count.
- Tests: `tests/test_box_lent.py` (racing processes never exceed, a killed holder frees, a machine-file
  name refused, no OS sample).
