---
name: DECISIONS-integrator-2026-10-26-night
description: Integrator decisions taken under the user's full-day autonomy grant (2026-10-26 evening onward), each with its reason, for the user's later review
created: 2026-10-26
accessed: 2026-10-26
tags: [integrator, decisions, autonomy]
metadata: {session: "https://claude.ai/code/session_014RWx71JHHMuBkrNmbYQkkA"}
---

# Integrator decisions, 2026-10-26 night (user: "fully authorised; record decisions; I review afterwards")

| # | decision | reason |
|---|---|---|
| 1 | Serve checkouts moved to `%LOCALAPPDATA%\lab-forge\repos` (user chose this option); dev-lab PR #453 removes `--root`/`[serve] root`, derives every lab-forge path from one `forge_home()`, serve refuses to run from a dev checkout | `~/src` was a second set of repos beside PEMC; a dev checkout's venv as the serve kit caused the A4 lock-up |
| 2 | G deployed from #453's branch before it merged (user: "直接部署") | G's reviews were the only way #453 could merge (bootstrap loop). After #453 merges, G's dev-lab checkout must be switched back to `integrate/main` |
| 3 | `~/src` contents rescued before deletion: unpushed branches -> `src-rescue/*` in each PEMC clone; 21 `output/logs` trees copied (no overwrite); `dev-lab.stale-20261022` (no remote, 1263 commits) bundled to `dev-lab/output/logs/26/10/26/src-retire/`; rows 124-126 -> lab-commons #464. `pr/adopt-security-check` branches abandoned (handoff). The final `rm -rf ~/src` was refused by the session's permission system and is left to the user | lossless retirement |
| 4 | motronics security differences declared (`exclude attic`, `ignore S311`) in `[tool.dev_lab.security]` (#4943) instead of per-site noqa | the repo ruff does not enable S311, so `# noqa: S311` trips RUF100 |
| 5 | dev-lab #455: a PR that adds security exemptions is released through `forge security release <pr> --why` | the step reads exemptions only from integration, so the first exemption PR could never pass (self-lock) |
| 6 | Hand relocks of motronics dropped: relocking is the forge's own `pr/relock-*` job; PR heads whose lock/fresh failed get `dep --lock-upgrade` on the branch | one party owns freshness |
| 7 | dev-lab integrate/main's five reds (surface budget, venv spelling, floor re-measure, provenance rows, private markers in memory) fixed on their own PR | every dev-lab PR was red on them |
| 8 | Review step to run on any host that can run the review command, not only the poller | the pollers (Ada) have no ai_lab, G has it but never polls: no head was ever reviewed |
| 9 | dev-lab #456: the five integrate/main reds plus `rustup check` exit 100 read as an answer | rustup exits 100 when an update is available (Ada, rustup 1.29): lock/fresh, the integrate/main relock and every auto-sync on Ada failed on it |
| 10 | motronics #4636 (field-circuit) merged with integrate/main by hand: `params.toml` sizing block = branch's machine units (ruling 2026-10-26) + IM's six-row R spread | NEWER-STRUCTURE; the sector-share line has no meaning in machine units |
| 11 | motronics #4664 (hairpin) NOT merged by the integrator | a live session (worktree `resync-4664`) pushed it at 23:27; its owner syncs it |
| 12 | Local private-marker hits outside `.claude/memory` (README, docs, src) left untouched | this box's denylist is wider than the forge host's; whether they are leaks is the user's call |
| 13 | Every motronics base run is INCONCLUSIVE: collection aborts on "108 heavy test files state no reason"; fix delegated on `pr/heavy-census-states-every-reason` | the producer exits 3 before any test runs, so no motronics PR can merge |
| 14 | G's serve kit (forge-home `repos/dev-lab`) tracks `pr/forge-host-bootstrap-1026` = #453 + #456 + #455 + #457 merged; switch it to `integrate/main` once all four land | the PRs that make reviewing possible need a review to merge; only a host already running them can give it |
| 15 | #4636 over-cap review taken over by the integrator: round 1, no blocking finding (8 non-blocking), hold released | user ruling 2026-10-26: over-cap heads are the integrator's |
