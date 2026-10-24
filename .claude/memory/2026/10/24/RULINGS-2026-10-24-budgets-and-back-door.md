---
created: 2026-10-24
type: decision
---

# User rulings 2026-10-24 (second round): budgets, auto-merge everywhere, integrator back door

Follows `RULINGS-2026-10-24-toolchain-decisions-and-backfill.md`.

1. `box.demand.Demand` is deleted together with P2 (box/compute move into dev-lab), in the same cut;
   `compute.model.Demand` stays as the one budget type.
2. The only default cap is a DAILY TOKEN cap. No default daily core-hour cap.
3. Tokens are counted as a number of tokens, not as cost (no price table).
4. Sub-agent concurrency is NOT a constraint: a human sets it per session. No seat/agent cap mechanism.
5. Every related repo runs with `pr_merges_since` (auto-merge, and auto-sync with it). As checked on
   2026-10-24: already declared in dev-lab, lab-commons, ai-lab, numerics-lab, optimi-lab, motronics;
   wdg-lab declares it in `pr/adopt-pr-only-flow` (#44).
6. Build the P5 integrator back door now (one named door, who and why recorded on the PR; one-PR and
   no-stack never block it) rather than skipping hooks.
