---
name: HANDOFF-integrator-2026-10-26
description: Integrator handoff at the end of 2026-10-26 -- state of every repo and host, mechanisms landed today, open gaps by priority, user decisions owed, and the traps a successor must know
created: 2026-10-26
---

# HANDOFF: integrator, end of 2026-10-26

Read first: `DECISIONS-integrator-autonomous-day.md` (this folder, rows 52-86), the plan
`../../21/PLAN-a-short-robust-toolchain.md` (§6 amendments A1-A10, all ACCEPTED by the user 2026-10-26),
the resource design `../25/DESIGN-one-resource-path.md` (#413).

## 1. Where everything stands (integrate/main)

| repo | head after this wave | notes |
|---|---|---|
| dev-lab | 7ef2cd9 | #413 P1 consumers, review redesign (findings v3, blocking, rounds, dispose), security forge step, metrics recording, hand-back hook, ORIGIN-HOLDS-ONLY-DECLARED-BRANCHES pre-push, outbox drops refused calls, stall watchdog, tick period/served repos as data |
| lab-commons | cf799bc+ (8d5ebdf rescue, f0c2ce2 P1) | #413 P0+P1 engine (`lab_commons.resources`, `box.external`), ledger schema only grows (`test_box_store_is_additive`), per-test wall ceiling 7200 s, top-ceiling timeout is final |
| ai-lab | 8f97653 | findings v3 (severity/security/rule/anchor, stable `F-` ids) |
| motronics | dff9324515 | P1 adoption, native tet walk bounded + input snap, cage contact not tangent, relock onto ea264f8 rules |
| numerics-lab / optimi-lab / wdg-lab | relocked 10-26 (c9eaa7f) | guard adopted everywhere |
| cc-market | v4.2.0 | sharp-review reads v3; report sink is output/logs |
| cc-config | fa04ec0 | launchers spawn `python -m ai_lab.session` |

A final relock of all six consumers onto dev-lab 7ef2cd9 was in flight at handoff (branches
`pr/relock-final-2026-10-26`, worktrees `.claude/worktrees/relock-final`); check whether it landed
(`git merge-base --is-ancestor`), else finish it.

Hosts: Ada six accounts redeployed on dev-lab 7ef2cd9, `[schedule] tick_minutes = 15`, one served set per
account (ezzls2+ezxmb14 motronics; ezxmw9 dev-lab; ezaxl22 lab-commons; alyyw103 wdg-lab+numerics-lab;
ezztz optimi-lab+ai-lab), all on `uon-free` (no production account yet). G serves from the lab-commons MAIN
checkout's `.venv` (resynced to dev-lab c9eaa7f today). WS1/WS2: handoff dev-lab #140, deployment not
confirmed. ai-lab runtime (`Documents/PEMC/ai-lab/.claude/worktrees/runtime`, also the provider cache) is on
8f97653 (editable); its dependency `--sync` was refused by a live process -- run it when idle.

## 2. Mechanisms landed today (the why is in the decision rows)

- Hung jobs: per-test wall <= 7200 s, outside kill fires in native code; stall watchdog scancels a task whose
  stream stops; an id cut at the top ceiling is a final TIMEOUT; shard walls held at the longest ceiling.
- Review: over-cap and blocked heads are integrator work (`forge review takeover|dispose|blocked|held`);
  HIGH or security findings block merge; rounds re-review only new diff; one family review command.
- Security: `tests/security` forge step on the poller (not on compute nodes), OSV reachable from Ada
  compute (tested comp001) but runs on the serving host; repo differences in `[tool.dev_lab.security]`.
- Agents: a turn that hands the user a runnable command is blocked (Stop hook); remedies print absolute
  interpreters; pushes outside `pr/*` and declared branches are refused at pre-push.
- Resources (#413): one engine; providers BoxOS/ToolSeats/Lent/SlurmAccount; `box.external` for jobs a
  solver runs outside the process; no seat_margin, no share; `[reserve]` OS headroom kept (user ruling).
- Metrics: Break-Glass = hand action; Merged-Branch landings count; tests never write the live log; one
  report over every served repo (`python -m dev_lab.dev.metrics_report`).

## 3. Open gaps, by priority

1. **motronics integrate/main has no full forge verdict yet** (hung-job fixes now deployed). Watch it; fix
   reds. Then land hairpin #4664 and field-circuit #4636 (reviews released). Known red: the stale path list
   `tests/not_heavy_files_priced_2026_10_24.py`.
2. **A4: a host refresh fast-forwards code but never resyncs the venv** -- today it locked G in a
   chicken-and-egg (new lab-commons source, old dev-lab in the venv). Make the kit tick resync when the lock
   key moves. Also: `dep --bootstrap` refuses a dev-lab worktree targeting ANOTHER repo's main ("a worktree
   may not mutate main") -- the scope check is wrong.
3. **Forge serve clones live in `~/src`** (G's serve runs from the lab-commons main checkout a session also
   uses -- that is how it got stuck). Proposed: `%LOCALAPPDATA%\lab-forge\repos\<repo>` / XDG
   `~/.local/share/lab-forge/repos/<repo>`, one default in dev-lab; user to confirm before deploy.
4. **Shared motronics `.git/hooks` is a stale shim** (`lab_commons.dev.githooks`): commits from any fresh
   worktree fail; agents install worktree-scoped hooks instead. Reinstall the shared hooks once every
   session's venv carries dev_lab.
5. **Live-run guard judges a process by its START cwd** (a run started in A that cd's into B holds A).
6. **Re-run the P0 baseline** on the fixed recording; then the P3 guard-by-guard review on fire/false-positive
   rates. Gitea pre-receive hook (approved; show the exact VPS change to the user first).
7. **#413 P2-P4** (consumables, persistent footprints, learned for every name), then `forge try`;
   motronics JMAG solve shape on `box.external` (ruling memo motronics 0ac4ca1cef) after the fresh-Designer fix.
8. **Plan A3** rename `[tool.lab_commons.*]` dev-lab tables to `[tool.dev_lab.*]`; **A7** Break-Glass posts
   on each carried PR; **A9** derived registries + rule-vs-mechanism census.
9. **Review steps 3-4** (declared risk tiers; recurring findings -> issue -> rule). ai-lab v3 imports the
   dev-lab registry at load -- pass the id set from the caller instead (layering).
10. motronics memory TRACKED-CITES-NO-EPHEMERAL rewrite (approved, dated batches); local debris reclaim
    (archive output/logs home, verify, then remove); uv version drift across hosts (lock revision 5 vs 3).

## 4. User decisions owed

- Confirm the forge clone relocation (gap 3) before it is deployed.
- Ada production account: user notifies when granted.
- Gitea pre-receive: approved in principle; show the VPS diff first.

## 5. Traps (each cost hours today)

- **Test the dependents before publishing a merge**, not only the changed module (52 forgeserve reds shipped).
- **The shared machine ledger** `C:/ProgramData/lab-commons/box.sqlite` is read by every lab-commons
  version on the box: schema changes are additive only; tests use their own `BOX_ENV` ledger.
- **Never touch shared `.git/hooks` or a main checkout** without the user; worktree-scoped hooks instead.
- **Fetch before you merge**: other sessions land PRs on integrate/main; a push refused by
  SESSION-HISTORY-ONLY-GROWS means re-merge on the new tip and re-verify.
- **Where a check runs follows what it depends on**: a per-head property is a forge step, never a test
  shard on compute nodes.
- **Rule text vs mechanism**: verify a "NOT ENFORCED" or "enforced by X" claim against code before citing it.
