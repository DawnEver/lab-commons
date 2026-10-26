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
