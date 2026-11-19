---
name: Submission cancellation and queue cancellation have distinct doors
description: Repair automatic merge route shadowing without losing either cancellation scope or its safety controls.
metadata:
  type: project
---

Exact source 9bbe1f98 was measured through Actions43757143171, job132943600952: canonical FAIL, three failures and 46 passes across the complete test_hpc_span.py and test_hpc_cancel.py owners, environment 587a42442ce4f792. The earlier two failures no longer appear. The new three counterexamples demonstrate automatic composition routed every cancel request to the default queue parser, hiding the incoming submission-record implementation. The retrospective AST merge audit's zero deviations did not establish route reachability.

The scopes are different facts: cancel -c reads owned submission placement records, cancels active or pending-only shards and persists CANCELLED state for status/gather; cancel-queue reads a user's Slurm queue, selects named jobs or oversized pending asks and retains ownership, grant/target, timeout and dry-run controls. The queue CLI, usage, log and current caller are migrated atomically to cancel-queue. There is no old CLI alias, flag guessing or fallback. The underlying queue implementation remains its single owner. New public-entry routing plants guard both directions; all original cancellation assertions remain.

No actual HPC cancellation or local test execution was performed. Static Ruff and whitespace checks pass. The new candidate requires ROOT's exact-SHA hosted replay of both complete owners; the previous 46 passes do not qualify this new source.
