---
name: the-cluster-home-has-owners-and-a-lease-door
description: Ada's home quota filled from a hand-built ~/ab; lab_commons.hpc now leases ad-hoc trees, guards the quota at soft/hard, and names strays every tick
created: 2026-11-06
accessed: 2026-11-06
tags: [hpc, quota, lease, ada, incident]
metadata: {}
---

# The cluster home has owners and a lease door

## Incident (2026-11-05/06, Ada `ezxmb14`)

- Home GPFS quota at its file limit (4,040,000 hard, 4,000,000 soft). `~/ab` 2.3 M files (`~/ab/cn`: 69
  worktrees, ~23 k each), `~/ci` 554 k, `~/src` 455 k, `~/.cache` 263 k. The forge cron could not write its log.
- `~/ab` was built by hand over ssh; `run submit` works under `~/ci`, which dev-lab retention reaps every tick.
  Nothing owned `~/ab`. `hpc clean` existed but needed an `[hpc]` table the workstation did not have.

## What landed (branch `pr/hpc-home-quota`)

- `lab_commons.hpc.home`: `QUOTA` (soft 0.80, hard 0.90), `MANAGED` roots with their owners, `STRAY_FILES`
  (50 k), `Usage` and the quota parsers (moved here from dev-lab `diskusage`, one copy), the capped census.
- `lab_commons.hpc.lease`: `~/ci/leases/{trees,records,reaped}`; record written BEFORE the tree; the reaper
  keeps a tree any live lease or any queued/running job (path prefix, not substring) holds; each removal is a
  line in `reaped/<yyyy-mm-dd>.jsonl`.
- `lab_commons.hpc.homedoor`: `guard` (reap at soft, refuse `HOME-QUOTA-HARD` at hard with top consumers),
  `home_pass` (the tick: reap, read, census), CLI `tree acquire|release|list|reap` and `home`; `--at user@host`
  needs no machine file. `run submit` passes the guard (`NotSubmitted`, INCONCLUSIVE). `clean --at` too.

## Measured on Ada (read-only, 2026-11-06)

- `mmlsquota -Y` prints the right `USR` row and EXITS 19 (other filesystems it cannot read); only rows are
  trusted. Fixtures: `tests/_hpc_fixtures/ada-*-2026-11-06.txt`. `quota -s` printed nothing. Grace `none`.
- `du --inodes -d1 ~` over ~3 M files did not finish in 280 s, so the census caps each walk (`find | head`).
- `$TMPDIR` is unset on the login node; inside a job it is the node-local scratch.

## Why the soft quota is the ceiling

- GPFS refuses writes at the soft quota once grace expires; the fraction is taken against it, not the hard limit.
