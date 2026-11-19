# A cluster home has owners; ad-hoc trees go through the lease door

INCIDENT 2026-11-05/06 (Ada): agent lanes built `~/ab` by hand over `ssh` (69 worktrees, per-tree uv caches,
cargo targets; 2.3 M files). Nothing owned it, so nothing reaped it, and the 4,040,000-inode home quota filled
until the forge cron could not write its own log.

- **A commit tree on a cluster is LEASED, never hand-made.** `python -m lab_commons.hpc tree acquire <repo-url>
  <sha> --purpose "<why>" [--ttl <h>]` puts it under `~/ci/leases` from the repo's one bare cache, and the tick
  reaps it once every lease has expired and no job works under it. Work that lives inside ONE Slurm job checks out
  into node-local `$TMPDIR` instead. Never `git worktree add`, `git clone`, `uv venv` or a cargo `--target-dir`
  into a home path (`lab_commons.hpc.lease`; dev-lab's `UNMANAGED-HOME-TREE` refuses the raw verbs).
- **The thresholds and the owned roots are declared ONCE**, in `lab_commons.hpc.home` (`QUOTA`, `MANAGED`,
  `STRAY_FILES`). Past `soft` a door reaps, then warns; past `hard`, `tree acquire` and `run submit` are refused
  by name (`HOME-QUOTA-HARD`) with the top consumers. A reader that finds no quota warns; it never reads zero.
- **A directory outside the managed roots past `STRAY_FILES` is a defect** (`STRAY-HOME-TREE`), named on every
  tick with its remedy. It is named, never deleted: removal is `python -m lab_commons.hpc clean --root <home>
  <name>`, which refuses while a job runs under it.

Cites, from the shared registry: `FIX-THE-CAUSE`, `REGISTRY-OWNS-THE-DECISION`, `UNSUPPORTED-RAISES`.
