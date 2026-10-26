---
created: 2026-10-24
type: handoff
---

# HANDOFF: integrator + toolchain refactor, state at 2026-10-24 ~14:00 UTC

Plan: `../21/PLAN-a-short-robust-toolchain.md` (P0-P6). Decisions with reasons: `../22/DECISIONS-integrator-autonomous-day.md`
rows 1-80. Principles: first principles, no historical baggage, Occam, one source of truth, layered decoupling,
names match, no backward compatibility.

## 1. Plan status by phase

| phase | state | evidence / gap |
|---|---|---|
| P0 baseline metrics | NOT DONE | no refusal/hand-action JSONL exists; nothing to re-measure against |
| P1 one session one PR, no stack, auto-sync | PARTIAL | `LAB_SESSION` + trailer live; auto-merge live. GAPS: auto-sync (merge IM + relock + push) does not exist -- every salvage branch below was synced by hand; `pr/review-redesign` is STACKED on #35 (violates no-stack; open it only after #35 lands) |
| P2 three-layer split | MOSTLY | dev-lab exists, serves the fleet, consumers numerics-lab/ai-lab/optimi-lab landed. GAPS: motronics #4802 not landed; wdg-lab #44 blocked; `[tool.lab_commons.*]` -> `[tool.dev_lab.*]` rename not started (one cut, own PR); rendered family files still committed |
| P3 shrink local gates | PARTIAL | dev-lab #9 landed. GAP (bites today): motronics pre-push still runs `lock` freshness and `branchset` locally from old lab_commons hooks -- every motronics salvage push refused (row 80) |
| P4 one model-access engine | MOSTLY | cc-market 4.0.0 merges through ai-lab; schema-v2 reader in dev-lab. GAP: stale forge hosts still say "reader knows [1]" |
| P5 capacity truth / fleet | PARTIAL | box ledger, deploy-fleet, all 6 Ada accounts on dev-lab 133e606. GAPS: `forge try` never-blocking not built; local measure BLOCKS (G 0-1 GB free; JMAG pid 42908 holds 1.74 GB to 2026-10-25 10:16Z) so no agent verified anything locally today; HPC claim latency 12-20 h on motronics |
| P6 truth in the tree | PARTIAL | TRACKED-CITES-NO-EPHEMERAL live. GAPS: rule-catch accounting, derived registries |

New today, outside the plan's list but in its spirit:
- PR review redesign (dev-lab `pr/review-redesign`, pushed, stacked on #35): own-range only, generated files derived from
  `famfiles.PROJECT_FILES`, incremental from last reviewed head, slices (`max_diff_bytes` = one slice, `max_total_bytes` 4 MB),
  synthesis pass, strict/standard/light lenses, hold as own context `tests/review-hold`. ai-lab #40 adds `--paths/--lens/--findings`.
  Not effective until #35 -> redesign -> consumer relock -> each repo declares `{paths} {lens}`.

## 2. Open PRs and branches (what lands next)

| repo | item | state | next action |
|---|---|---|---|
| dev-lab | #35 `pr/integrator-followups` abe8ee4 | base green, workstation part pending (G memory) | lands by forge; then open review-redesign PR; then drop 1d69173 overlap vs stoprun branch |
| dev-lab | `pr/stoprun-proves-an-orphaned-worker` 054444d | pushed, IM merged | open/let forge land; supersedes #35's 1d69173 |
| ai-lab | #40 review slices | open | forge |
| motronics | #4802 units-step4 + dev-lab cutover 91ed574efa | Ada ezxmw9 running 34/68 | THE GATE: everything motronics below waits on it |
| motronics | #4694 dc929870af, #4687 b6ce948e9b | Ada pending | forge |
| motronics | local, unpushed: `pr/im-declarations-green` 63c76c9129 (16/21 IM declaration reds), `pr/4696-remainder` ad7530d9b9, `pr/case-ipm-uv-hairpin-48s8p` 70d57beb27, `pr/branch-names` be07ef23e9, ferrari f27e611eb5, tesla redo 0c5c8e6b64 (`integ/tesla-copper-redo`) | blocked on #4802 + hooks | after #4802: reinstall shared hooks from dev-lab, merge #4802 in, `dev_lab.dev.dep --lock-upgrade`, push |
| lab-commons | `pr/a-named-capacity-is-a-ledger-resource` 89c3622, `pr/worktree-archive` aa571ef | pushed | forge |
| lab-commons | `pr/html-report-mechanism` 243ff96 | pushed, no PR (#341 owns refactor/*) | #341's owner takes it |
| lab-commons | `pr/a-stage-may-reserve-memory-without-cores` | IM built FIFO full-reservation; branch has deadline-aware backfill (7/54 tests fail on IM design) | USER: keep IM design or port backfill |
| lab-commons | `pr/docs-one-flow`, box-resources LENT lines | not in dev-lab | port to dev-lab docs |
| optimi-lab | `pr/adopt-pr-only-flow` e7377c5 | pushed | forge; workstation hook-install red is runner drift |
| numerics-lab | `pr/exact-rank-certificate` c9cb576 (noise-rows folded) | pushed, inner merge b27f519 lacks trailer | forge |
| wdg-lab | #44 + 3 branches | #44 blocked by origin branch `refactor/html-report-mechanism` (#49) | land adopt-pr-only-flow first |
| motronics | `feat/wyx-mesh` | FROZEN by user | do not touch |

Superseded and closed today (audited, nothing lost): motronics #4652 #4654 #4661 #4666 #4669 #4682 #4697 #4698;
branches with unique commits were kept by the lossless tool.

## 3. Gaps, ranked, with the first-principles fix

1. **Local gates still decide what the forge should** (P3). The newest-lock pre-push makes every motronics push race
   lab-commons; branchset reads an old default. Fix: lock freshness and branchset on the forge only; auto-sync relocks.
2. **No local verification** (R8). Fix: `forge try` (P5) so a refused box is an answer, not a stall; a JMAG app hold
   should not starve all verification for 24 h.
3. **No auto-sync** (P1). Today one agent hand-merged IM into ~20 branches. Fix: the forge merges IM into a behind head.
4. **HPC claim latency** -- motronics PRs wait 12-20 h. Measure claim->start per account before changing anything.
5. **motronics IM red** -- 5 declaration reds left, three need USER rulings:
   - dependency pins in pyproject (`test_no_source_claims_a_dependency_pin` vs `_REV` comment allowing `@sha`);
   - `reference = "vendor=jmag"` in 20 validation.toml: leg spec or vendor-rung selector;
   - Cargo numerics rev e623f3c vs uv.lock 7853fc4 (decided: align to uv.lock; needs a Rust build on the forge).
6. **`[tool.dev_lab.*]` rename** (P2) -- ruled: one cut, own PR, after the migration lands.
7. **Units refactor steps 2, 3, 5**; 7 hamilton tolerances parked in `scratch/`.
8. **Consumers re-run `agent_guard --install`** in their dev-lab bump PR (#35 makes a missing stray hook report STALE).
9. OneDrive `claude_settings.json` -> proposed version once repos render `automode.json`; global autoMode rows -> per-repo.
10. Promote `main` (all repos lag IM; motronics by 5419 commits) once each IM tip is green.

## 4. Pending user questions

- Reinstall the shared motronics `.git/hooks` from dev-lab now or at #4802 landing.
- Relax pre-push lock freshness to "not behind integrate/main" (or move it to the forge, per P3).
- The three rulings in gap 5, and the stage-memory design.

## 5. How to resume

Statuses: `<lab-commons venv> C:/Users/linxu/.claude-evans/jobs/00353973/tmp/fails2.py <sha>` from the repo; failing cells:
`cells.py <sha> tests/base`. Push: motronics `sh scripts/hooks/with-retry.sh push`, others
`<dev-lab venv> -m dev_lab.dev.netverb -- git push`. Close superseded: `-m dev_lab.dev.forge pr close-superseded N --by M`.
Worktrees holding the unpushed work: `motronics-studio/.claude/worktrees/integ-*`, `dev-lab/.claude/worktrees/{next-integrator,review-redesign}`.

## 6. Update, evening 2026-10-24 (decision rows 81-93)

- LANDED: dev-lab #35 (d720e99); lab-commons #354 OOM retry asks more (4dba818); dev-lab break-glass relock onto it (68cac18).
- OPEN: dev-lab #41 review redesign (base green); ai-lab #40 review slices (workstation red was a local integ/relock branch, removed; needs a re-run).
- motronics #4802: first full verdict, 45 cells red that IM has green; fix agent on worktree `pr-units-step4`, unpushed.
- motronics #4694: our 14 fixes merged into the F7 session head and pushed as 543062fba3 (worktree-scoped hooks); verdict pending; review held (5.9 MB).
- pr/im-declarations-green: 16 declaration fixes + causes 2-5 + numerical 6-8, unpushed (waits on #4802).
- Ada accounts NOT yet on dev-lab 68cac18: the deploy loop was reaped for low G memory. Rerun
  `C:/Users/linxu/.claude-evans/jobs/00353973/tmp/cut/deploy_devlab.sh` per account when memory allows.
- Shared motronics hooks need NO reinstall: `dev_lab.dev.hook_install --install` in a linked worktree is worktree-scoped.
- User questions still open: ngspice on Ada; lock-freshness at push; CNT two-airgap band test; full-ring polar block tests;
  dependency pins; `vendor=jmag` reference meaning; lab-commons stage-memory design.
