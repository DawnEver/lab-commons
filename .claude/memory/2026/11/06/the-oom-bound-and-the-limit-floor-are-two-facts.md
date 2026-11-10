---
created: 2026-11-06
corrects: .claude/memory/2026/11/06/the-oom-bound-is-dropped-for-integrates-limit-floor.md
---
# The OOM bound and the limit floor are two facts

- User ruling 2026-11-06 (milestone): `gather.oom_bounded` / `run.OOM_ROUNDS` is restored beside integrate's
  4e7c5829 limit floor. The floor sets the memory ASK (a kill floors at its limit's peak x `OOM_GROWTH`); the
  bound stops endless REQUEUE rounds (a lone id killed for memory in `OOM_ROUNDS` rounds settles
  `OUT_OF_MEMORY`). Neither implies the other.
- `oom_bounded` now takes `shard_of`/`states` and derives the non-TIMEOUT items itself, so `gather_run` stays
  within PLR0915.
- Measured at the restore: the bound's test and its neighbours pass; three `test_hpc_run.py` tests
  (throttle-raise, memory-class arrays, folded-round-teaches) fail order-dependently on the milestone WITHOUT
  this change too -- a learned-table leak between tests, not this restore.
