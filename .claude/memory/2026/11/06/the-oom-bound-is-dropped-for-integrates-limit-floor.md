---
created: 2026-11-06
corrects: .claude/memory/2026/11/06/a-lone-id-oom-killed-below-the-ceiling-is-bounded.md
---
# The OOM bound is dropped for integrate's limit floor

- At the merge onto integrate 669a07e8, integrate's 4e7c5829 already fixed the same incident at its cause: the
  read is the pytest child's, so `Measured.learn` floors a kill at the peak its limit allowed x `OOM_GROWTH`, the
  ask reaches the ceiling in log2 rounds, and `fold` settles the lone id `out_of_memory`.
- `run.OOM_ROUNDS` / `gather.oom_bounded` and its test were dropped as a second mechanism for that fact. This also
  answers the earlier entry's open question (why the ask never grew past ~435 MB).
