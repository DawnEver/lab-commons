---
created: 2026-11-06
---
# A lone id OOM-killed below the ceiling is bounded

- Seen on motronics ca127cc9f5 (forge `base` run): the same 7 ids OOM-killed at ~435 MB in rounds 3-11; the run
  never closed. `gather_run` exempts OOM rounds from `RETRIES` while a grant states a ceiling, and a lone id is
  only settled by `fold`'s `alone` path once its band is AT the ceiling -- a kill the ask never reaches (an
  item-level kill below the band's ask) loops forever.
- Fix: `lab_commons.hpc.gather.oom_bounded` counts, per id, the OOM rounds it was re-queued after running ALONE
  (not in a TIMEOUT shard) in `state['oom_lost']`; at `run.OOM_ROUNDS` (3) its cell is `OUT_OF_MEMORY`
  `killed for memory alone in 3 rounds` and the run can record. Bisection of multi-id groups is unchanged.
- Test: `tests/test_hpc_run.py::test_a_lone_id_oom_killed_round_after_round_below_the_ceiling_settles_out_of_memory`.
  Not run locally (caller's instruction); the forge verdict is the measure.
- Open: WHY the ask did not grow past 435 MB for those ids was not diagnosed here.
