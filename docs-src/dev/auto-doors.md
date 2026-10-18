# Auto mode runs the doors

In Claude Code and Codex auto mode, the family's checked doors run without a prompt or a classifier refusal
(`AUTO-MODE-RUNS-THE-DOORS`). Use the door instead of the raw verb: each re-checks its precondition at the
moment it acts. Local cleanup is a door; deleting a branch on origin stays a human's action, and a subagent never
pushes.

## The doors

| door | does |
|---|---|
| `python -m lab_commons.dev.branchset --apply` | Deletes every merged LOCAL branch: never a declared branch, never one with commits origin lacks, never one checked out in any worktree. Re-runs `merge-base --is-ancestor` right before each delete and pins it to that sha. Origin candidates are listed with `git push origin --delete <b>` for a human. |
| `python -m lab_commons.dev.worktrees [--prune] [--archive] [--expire-archive]` | Lists the worktrees. `--prune` removes one only when it is clean (`git status --porcelain` empty), holds no ignored content but regenerable artefacts (caches, `.venv`, `*.egg-info`, `build/`, `target/`, plus `[tool.lab_commons.worktrees] regenerable`), and its HEAD is on a remote branch. Removal is `git worktree remove` without `--force`; an unregistered empty directory is removed with `rmdir`. Anything else is listed with what blocks it. By default the door never moves work aside. `--prune --archive` is the opt-in exception (needs `archive` and `archive_days` declared, else it refuses naming the key): a tree blocked ONLY by ignored work, not locked and named by no live process (command line, and cwd where `/proc` publishes it; an unreadable table refuses; on Windows a cwd inside the tree fails the rename), has its regenerable paths deleted, the rest RENAMED into `<archive>/<YYYYMMDD>/<tree>/` beside a `WORKTREE.txt` (HEAD, branch), the move verified, and only then `git worktree remove` without `--force`; any failure keeps the tree, and no branch is deleted. `--expire-archive [--prune]` removes `<archive>/<stamp>/` older than `archive_days` by the stamp NAME, never mtime, purges regenerables from younger ones, and lists a name that is not a stamp without deleting it. Every run warns when `ceiling_roots` hold `file_ceiling` files, counted by a walk that stops there. |
| `python -m lab_commons.dev.stoprun --pid PID [--repo PATH] [--dry-run]` | Stops one identified run and its subtree, children first, never an ancestor or a sibling. A run is identified by its command line: the built-in signatures (verify, a gate runner, pytest) plus regexes in `[tool.lab_commons.stoprun] signatures`. Any other pid is refused (exit 3). |

The raw verbs (`git branch -D`, `git worktree remove --force`, `rm -rf`, `taskkill`) stay unallowed.

The default still never moves work because a move is only safe when the door can verify every step before the
removal, and it can only do that against a destination the repo declared. Declared once, in the main checkout:

```toml
[tool.lab_commons.worktrees]
archive = "output/worktree-archive"   # opt-in; relative to the main checkout, no default
archive_days = 14                     # required with archive
file_ceiling = 200000                 # optional; requires ceiling_roots
ceiling_roots = ["output"]
```

## One table, two clients

`lab_commons.dev.autodoors` holds `DOOR_MODULES`; `script_doors(root)` adds the repo's tracked `scripts/**.py`
with a `__main__` guard and the shell doors it names (`shell_doors=`, `--shell-door`). Both clients are
rendered from these:

- **Claude Code**: one NARROW `Bash(...)` row per door, e.g. `Bash(.venv/*/python* -m lab_commons.dev.worktrees *)`.
  Auto mode suspends broad rows but keeps a narrow one live. A launcher in another checkout is admitted only
  repo-relative, `.claude/worktrees/*/.venv/...`; run the door from the repository root.
- **Codex**: one `prefix_rule(pattern = [...], decision = "allow")` per door in `.codex/rules/family-doors.rules`;
  `codex execpolicy check --rules <file> <cmd>` probes it.

No rendering may promise a raw verb (`promises_raw`).

## Adopting

1. Construct `AllowAdoption(..., scripts=script_doors(root, shell_doors=(...)))`, re-render
   `.claude/settings.json` with `permissions_block`, and drop any declared row that now duplicates a door row.
2. Run `python -m lab_commons.dev.autodoors --repo . [--shell-door <path>] --write` and commit
   `.codex/rules/family-doors.rules`; without `--write` it exits 1 when the file is stale.
3. Delete local copies of the doors.
