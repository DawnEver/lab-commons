---
name: DECISIONS-integrator-autonomous-day-2026-10-26
description: Every decision the integrator took on 2026-10-26 under the user's standing autonomy, for after-the-fact review (continues 2026-10-25 rows 1-51)
created: 2026-10-26
---

# Integrator decisions, 2026-10-26

Authority: user, 2026-10-26 -- "持续迭代直到完成全部任务, 完全授权你去做决策, 记录下来, 事后审核", and
"pr/relock-* pr/one-field-* 这种 pr 你也需要自己合并一下". Every PR in all seven repos lands by local
Break-Glass merge (row 48 of 10-25). Merge principles applied throughout: newest structure, first
principles, no backward compatibility, single source of truth, names match reality.

| # | Decision | Why |
|---|---|---|
| 52 | Every open PR across the seven repos is integrated per repo in one round, prepared by one subagent per repo, published by the integrator as ONE merge commit | one first-parent merge per round passes `_forge_merges_only`; subagents do not push |
| 53 | dev-lab: merge #109 (lab-commons relock) only; close #54 #113 #119 #120 #122 #125 #127 #134 #135 #136 #140 #143 as superseded by db063ee | their content landed in 65ff29a; `git merge-tree` showed the only remaining delta was #109's lock, picked up by forge sync merges |
| 54 | The forgeserve test compares the capacity through JSON (`json.loads(json.dumps(asdict(BOX)))`) | the child receives it as JSON; the new `Capacity.watched` tuple reads back as a list -- the test now states the real boundary |
| 55 | lab-commons: merge #425; close #412 #418 (only a uv.lock revision header left; #418's html already landed via #341) and #175 (steward->integrator landed through dev-lab #134) | content already on integrate/main |
| 56 | ai-lab: merge #40, keeping integrate's optional `--method` beside the PR's `--lens`/`--paths`; close #90 | #90 only re-adds `ai_lab/cli.py` and the `ai-lab` console script, deleted by the 10-25 ruling |
| 57 | wdg-lab: merge #44 on the newer dev-lab adoption; drop its second dev-lab override URL and `ephemeral_refs` block | NEWER-STRUCTURE; nothing on integrate/main or in tests needs them |
| 58 | wdg-lab #48 (cut-prod) NOT merged; returned to its author. The tested merge is kept at local `integration/local-2026-10-26-with-cut-prod` (5c078793) | 15 reds from its own content: HPC login emails and home paths in memory and `scripts/hpc_clean.sh`, 20 reflection hits, a second HTML report mechanism, undeclared skips, ratchets. Fixing them rewrites the author's work; that is not a merge resolution |
| 59 | TREE-HAS-A-LIVE-RUN judges the tree of the segment the rule fired on (`targetDir` in deny-commands.js), and reads `dep --bootstrap X` | it used the first named dir or the last `cd`, so `git -C other status && git merge x` judged `other` |
| 60 | P0 metrics live at `<main checkout>/output/logs/<yy>/<mm>/<dd>/metrics/<kind>.jsonl`, one log per repo, through one `log_file()`; the old `~/.config/lab-commons/metrics/` lines are no longer read | metrics were written beside the machine config; wdg-lab's tests point that into `tests/`, so they landed in a tracked tree |
| 61 | sharp-review writes its report to `output/logs/<yy>/<mm>/<dd>/sharp-review/` (ai-lab `report_path`, sink renamed `memory` -> `report`); cc-market `post-review.js` refuses a plan without `report`, with no fallback | a regenerable report is program output; only findings a person keeps become memory. Consequence: rem `todo` no longer lists sharp-review findings until a person moves one into memory |
| 62 | Ada redeploy HELD until the cadence was declared data. The HPC tick period is `[schedule] tick_minutes` (no new `[deploy]` key), the minute is staggered by crc32(home), and a repo entry may say `serve = false` | redeploying with the hard-coded `*/5` would have written ~500 ticks/h against the user's "reduce HPC pressure" ruling; one number already governs card staleness and the poll lease |
| 63 | The deploy's closing tick reads the same `ticked()` set as the crontab | it ticked every declared repo once, so every redeploy served unserved queues |
| 64 | Ada accounts: `tick_minutes = 15`; served repos: ezzls2 and ezxmb14 motronics-studio; ezxmw9 dev-lab; ezaxl22 lab-commons; alyyw103 wdg-lab and numerics-lab; ezztz optimi-lab and ai-lab; every other repo `serve = false` (the dev-lab kit keeps a refresh-only line). Machine files backed up as `config.toml.bak-20261026` | turns the 10-25 hand-cut crontab into declared data at about the same pressure: 8 serving lines + 5 kit refresh lines at */15 = 52 ticks/h |
| 65 | The ai-lab provider-cache runtime update, cc-config merge and cc-market release are DEFERRED | the runtime is shared by every live session; updating it under one can break that session, and no human is present to confirm none is running |
| 66 | Ada redeployed on dev-lab 1cebb7b through `deploy-fleet`; verified: 13 marked cron lines, all `*/15` with per-account offsets (6, 6, 9, 12, 13, 14), every first tick of a served repo exited 0 or 1 (an older tick held the flock) | the formal mechanism replaces the 10-25 hand-cut crontab at the same pressure |
| 67 | The outbox keeping forge-refused merge POSTs of long-closed PRs forever is a defect, fixed at the source (a definitive refusal is final, only transient failures stay queued) | seen on every Ada tick: 13 stale merge calls retried per tick across five repos |
| 68 | motronics round published as afb5a98de3 with local measure covering only coupling and rotation; the architecture tier left to the forge part | a low-memory reap stopped the agent's sweep; `measure tests/architecture` reports the selection is a forge PART ("over the 300 s wall ... produced by the forge after a push"), and an integrate/main push needs no part |
| 69 | motronics: newer upstream shape wins (units-step4 box Demand, per_step.py), the lane's duplicate sector fraction yields to `terminal_share`, the stale #4861 lock is replaced by a fresh `--lock-upgrade` on the tip; every dropped or rewritten test is named in the merge messages | NEWER-STRUCTURE, single source of truth, MERGE-DEVIATIONS-NAMED |
| 70 | #4664 hairpin and #4636 field-circuit stay OPEN | both lanes pushed after this round; they sync onto afb5a98de3 and land next round |
| 71 | 10 pre-existing reds (`tol_rel` passed to `monolithic_march`, which refuses it) are not fixed in this round | red on 431d2871c2 and on the lane tip alike; they belong to whoever owns the march API change, recorded here so they are not lost |
