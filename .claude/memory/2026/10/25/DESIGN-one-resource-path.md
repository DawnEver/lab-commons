---
name: DESIGN-one-resource-path
description: Every consumer of every resource -- forge parts, box runs, HPC jobs, campaigns, agent sessions, model calls -- goes through one path: declared demand, measured supply, one admission rule, enforced limit, recorded actual, learned next demand. Three kinds (occupancy, consumable, persistent) differ only in how supply is computed and when it returns.
metadata:
  type: project
created: 2026-10-25
accessed: 2026-10-25
---

# DESIGN: one resource path (user ruling 2026-10-25)

RULING: EVERY consumer of a resource goes through ONE path; there is no second mechanism anywhere.
This page makes that concrete for lab-commons, dev-lab and ai-lab, and lists what is deleted. It is the
detailed form of PLAN-a-short-robust-toolchain P5 ("HOW MUCH may run is the box ledger only"), widened
from cpu/mem/seats to every countable thing.

Read against origin/integrate/main of lab-commons, dev-lab and ai-lab on 2026-10-25. The ai-lab daily
token cap (`pr/daily-token-cap`) is already merged into ai-lab integrate/main (`src/ai_lab/budget.py`);
no `pr/measured-tool-seats` branch exists on lab-commons origin, so measured seats are taken as they stand
on integrate/main (`box/demand.py:139` `seat_supply`, probe-or-declared) and `seat_margin` is folded in
below as the one per-name margin.

## 1. The five parts, identical for every resource

```
declared demand (per task) -> measured supply (per provider scope) -> admit (ONE rule)
   -> enforced limit (per run) -> recorded actual (one ledger) -> learned next demand (one table)
```

- DEMAND is a `compute.model.Demand` -- a named vector. Nothing else carries a size.
- SUPPLY is a `compute.model.Supply` -- vector plus present names -- produced ONLY by a provider plug.
- THE RULE is `compute.admission.admit`, unchanged in shape:
  `needs <= present and demand <= supply - running`. `running` means "held and not yet returned",
  whatever the kind.
- THE LIMIT is the demand itself: what was admitted is what is enforced (a claim is never exceeded).
- THE ACTUAL is what the enforcer measured at the end, written to the one ledger.
- THE NEXT DEMAND is `compute.learned` keyed `(producer, need-set)`, now over every name, not cpu/mem only.

## 2. Three kinds -- one rule, three supply formulas, three return moments

A kind is a property of a resource NAME, declared once in lab-commons (`resource.KINDS`, a mapping
name -> kind; an unknown name is refused, `UNSUPPORTED-RAISES`).

| kind | names | supply (computed by the plug) | taken | returned |
|---|---|---|---|---|
| OCCUPANCY | `cpu`, `mem_gb`, each tool seat (`jmag`, `femm`, ...), OS (`windows`, `linux`) | measured now: `room()` -- OS free less undrawn declarations less margin; seats = probe-or-declared less held | at start | at run end (release or reap) |
| CONSUMABLE | `tokens`, `core_h`, `forge_requests`, `submits`, `wall_h`, `money` | a balance per WINDOW: `cap - used_in_window`, where `used = reported_at_t_r + local charges after t_r` | the per-run CAP at start | the unspent remainder at run end; the spent part never |
| PERSISTENT | `disk_gb`, `files`, `venvs`, `worktrees` | `quota - present use` (measured), less footprints declared but not yet written | the declared footprint at creation | by LIVENESS RECLAIM (owner dead / artifact retired), never by run end |

Consequences, stated once:

- CONSUMABLE admission is the same rule: `cap <= balance - sum(held caps)`. A held cap is a promise, so two
  sessions cannot both be admitted against the last million tokens. At run end the ledger turns the cap
  into a CHARGE of the actual and releases the rest.
- PROVIDER REPORT IS TRUTH, LOCAL LEDGER IS THE FAST STOP (ai-lab ruling 2026-10-24, generalized). The plug's
  `measure` returns `cap - max(reported, local)` -- concretely `reported_used(t_r) + local_charges(after t_r)`
  -- so a key shared by several hosts is bounded globally at the report's cadence and locally at once.
  Where no report exists (forge `core_h` from our own `sacct` reads is itself the report; `wall_h` has none),
  the local ledger is both.
- PERSISTENT returns on liveness: the ledger already has `persist(claim, probe)` and liveness reap
  (`box/ledger.py:198`, `:146-185`); a footprint row is a persisted claim whose probe is "the owner's
  artifact still exists and is in flight" (retention's IN-FLIGHT definition, `dev_lab.dev.retentionplan`).
- `money` and `wall_h` are kinds with no provider today; they exist as names so a provider can be added
  without touching the engine (assumption: no consumer needs them before a provider exists).

## 3. Wall: stays a separate REQUIRED field, precisely because it is not a quantity

Decision: wall does NOT enter the vector. It stays the claim's required `wall_s` (no default, as now,
`box/demand.py:78`).

Why, from first principles: every vector entry is SUMMED over running claims and compared with supply
(`supply - running`). Wall is the claim's duration; summing the walls of concurrent claims measures
nothing, and no scope has a "wall supply" to compare against. Wall is the TIME AXIS of a claim:

- it bounds every OCCUPANCY entry (the enforcer kills at `wall_s`; ledger rows end by `WALL_CEILING_S`);
- it DERIVES consumable charges: `core_h = cpu * wall_s / 3600` is the cap a cluster run asks; its actual is
  `cpu_s / 3600`. Where wall-time itself is budgeted (a box's daily share), the consumable is `wall_h`, a
  vector entry derived from `wall_s` by the engine -- declared once, never typed twice.
- for a CONSUMABLE-only claim (a session, a model call) wall is still required: it is how a held cap is
  returned when the holder dies (reap at deadline), the same liveness rule as everywhere else.

So the claim is `Claim(demand: Demand, wall_s: int, what, key)`; nothing else.

## 4. Layering and the plug interface

```
lab-commons  resource/       KINDS, Claim, the one engine (admit over every kind), the one ledger,
                             the learned table, the Provider protocol.  (compute.model/admission stay the core)
providers    implement Provider; declare which names they supply on which scopes
consumers    build a Demand (from repo data or learned) and call engine.claim(scope, claim)
```

The plug -- lab-commons `resource.Provider`, two methods, nothing else:

```python
class Provider(Protocol):
    names: frozenset[str]                       # the resource names it supplies, each in KINDS
    def measure(self, scope: str) -> Supply: ...                       # supply NOW for that kind's formula
    def enforce(self, scope: str, claim: Claim) -> ContextManager[Actual]: ...  # run within the claim; yield the actual
```

`Actual` is a `Resources` filled when the context exits (peak mem, cpu_s, tokens reported by the reply,
bytes written). The engine -- never the plug -- holds, admits, charges, releases, reaps and learns.
A scope with several providers (a box: OS + seats + disk) gets their `measure` results summed per name;
two providers claiming one name on one scope is refused at registration.

The providers, by repo:

| provider | repo | names | measure | enforce |
|---|---|---|---|---|
| box OS | lab-commons `box` | cpu, mem_gb, OS | `box.supply.room` (exists) | Job Object / systemd scope (exists, `box.run`) |
| tool seats | lab-commons `box` | each `[capabilities]` tool | `probe.seated` over `seat_supply` less `seat_margin` | the seat held for the run |
| workstation disk | lab-commons `box` | disk_gb, files, venvs, worktrees | disk usage vs machine-file quota | refuse creation past footprint; footprint row persisted |
| Slurm account | lab-commons `hpc` | cpu, mem_gb, submits, core_h, files | `probe.account` + `sacct` + `mmlsquota` (exist, moved behind the plug) | the allocation (`--time`, `--mem`) |
| forge API | dev-lab | forge_requests | `X-RateLimit-Remaining` / reset as the window | the client counts and stops at the cap |
| model tokens | ai-lab | tokens (scope `<provider>/<key-fingerprint>`) | provider usage report + local charges | `max_tokens` clamped to the held cap; reply usage is the actual |

Consumers only declare: a forge part (`Demand` from learned + its declared needs), an HPC job (same,
`core_h` derived from cpu x wall), a box run, a campaign (sum of its units' demands, each unit its own
claim -- a campaign holds nothing itself), an AGENT SESSION (`tokens` cap + `wall_s`), a MODEL CALL
(`tokens` cap; claimed WITHIN its session's claim when it has one, else directly on the key scope).

### ai-lab's token cap is a plug, not a special case

- `[ai_lab.daily_token_cap]` becomes the tokens provider's window caps (section 5); `budget.remaining`
  becomes `measure`, `budget.charge` becomes the engine's charge from `Actual`, the
  `tokens-<date>.jsonl` file becomes ledger rows. `DailyTokenCapReached` becomes the engine's one refusal
  (`NotAdmitted` naming `tokens (asked, free)`).
- A session is admitted iff its cap fits the key's balance less every held session cap; its calls draw
  from the session's cap (a child ledger row keyed by the parent claim), so a session can never overspend
  its own admission and the key can never be over-promised.
- SUB-AGENT CONCURRENCY IS NOT A RESOURCE (user ruling). Each sub-agent is a session claim on the same key;
  how many run at once is whatever the balance admits. No counter, no semaphore, no `max_agents` anywhere.
- Key slots (`ai_lab/client.py:34-42`) stay: a slot is a SCOPE, and falling to the next slot is choosing the
  next scope whose supply admits the claim -- the same walk `claimable` does over hosts.

## 5. Single source -- where each number is declared once

| number | declared in | read by |
|---|---|---|
| resource name -> kind | lab-commons `resource.KINDS` (code, one table) | engine |
| per-task demand prior + needs | repo data `[tool.lab_commons.demand.<task-kind>]` (pyproject) | learned (prior until measured) |
| measured demand | `compute.learned` table (machine-local state) | consumers via `learned.demand(...)` |
| host size, reserve/margins, seats, disk quota | machine file `[reserve]`, `[capabilities.<tool>] seats/supply`, `[storage] quota` | box providers |
| cluster account, partitions, qos | machine file `[[hpc.grant]]` (identity/reach only) | Slurm provider |
| consumable window caps | machine file `[resources.<name>]` per scope, e.g. `[resources.tokens] window = "1d"`, `default = ...`, `"anthropic/3f2a9c1b7d4e" = ...`; `[resources.core_h] window = "1d"`, `per_ref = {branch = 200}` | the provider's `measure` |
| provider truth | the provider's own report (usage API, `sacct`, rate-limit headers, `mmlsquota`) | `measure` |

Assumption: consumable caps are an operator decision about a key or account, so they are per-machine data
(the machine file), not repo data; `daily_budget_core_h` therefore moves OUT of `[tool.lab_commons.schedule]`
(a repo table) into `[resources.core_h]`. Schedule keeps ORDER numbers only (classes, aging, boosts).

## 6. Delete list -- every second path today

lab-commons (origin/integrate/main):
- `src/lab_commons/compute/admission.py:19` `STATED_ONLY = {'files'}` and its filter at `:56` -- a name is
  metered iff a provider on the scope supplies it; `files` becomes a PERSISTENT name of the disk/Slurm plugs.
- `src/lab_commons/compute/model.py:162-166` `submitted()` forcing `submits >= 1` -- `submits` is a CONSUMABLE
  of the Slurm plug; the demand prior declares it.
- `src/lab_commons/box/demand.py:130-150` `declared_seats` / `seat_supply` beside `compute/probe.py:57-101`
  `inventory` / `seated` / `tools` -- two seat readers; one seats provider remains.
- `src/lab_commons/box/ledger.py:212-224` `_decide`, `:325` and `src/lab_commons/box/order.py:54` each build
  `Supply('box', ...)` inline and call `admit` themselves -- they call the engine.
- `src/lab_commons/box/demand.py:73,107-120` "cpu and memory enforced, every other name is a seat" -- the
  name's kind (from `KINDS`) decides, not a hard-coded pair.

dev-lab (origin/integrate/main):
- `src/dev_lab/dev/forgeschedule.py:34,193-200` `daily_budget_core_h` / `over_budget` -- a second
  consumable mechanism; becomes `core_h` admission.
- `src/dev_lab/dev/forgerunstate.py:137` `standing()` core-hours-per-run and `src/dev_lab/dev/forgeslurm.py`
  standing assembly (supply + files headroom + core-hours, docstring `:7-12`, fallback `:278`) -- moves behind
  the Slurm plug's `measure`.
- `src/dev_lab/dev/forgetick.py:247-255` `_whole` -- fabricates a supply from `Cost()` defaults when none was
  measured; an unmeasured scope refuses instead.
- `src/dev_lab/dev/forgetake.py:235-241` its own `admit` + `self.taken` loop and
  `src/dev_lab/dev/forgetry.py:48-62` building `Resources` from a host card -- both become `engine.claimable`.
- `src/dev_lab/dev/retention.py` "the file budget ... read once and used twice" -- the quota read moves to
  the PERSISTENT plug; retention stays only as the RECLAIM (liveness) half.
- `src/dev_lab/dev/forgework.py:94` waiting out a rate limit after the fact -- `forge_requests` is admitted
  before the request.
- kept, because they already use the one path: `envseat.py:48`, `inflight.py:110`, `supervise/loop.py:394`
  (`hold({seat: 1})`), `verifybox.py:176`, `supervise/process/systemd.py:279` (consumer Demands).

ai-lab (origin/integrate/main):
- `src/ai_lab/budget.py` -- `_caps` (`:68`), `state_dir`/`ledger_file` (`:80-90`, a platformdirs JSONL
  ledger), `used_today`/`remaining`/`charge` (`:91-125`): the separate token ledger. What survives is
  `fingerprint` (scope naming) and the tokens provider plug.
- `src/ai_lab/client.py:367-375` the inline `budget.remaining` / `budget.charge` around a send -- becomes
  `engine.claim(key_scope, Claim(tokens=cap, wall_s))` with `enforce` clamping `max_tokens`.

## 7. Migration -- phases, each with its acceptance tests

No backward compatibility. Formats upgrade on read only where a STORED ledger format changes (`FORMATS-UPGRADE-ON-READ`).

P0 -- engine in lab-commons. `resource.KINDS`, `Claim`, `Provider`, engine `claim/claimable/charge/reap`;
ledger gains a `kind` column and a `window` column. Upgrade on read: a pre-existing `claims` row is
OCCUPANCY. Tests: one parametrized test runs the same admit/hold/release/reap script against a fake
provider of each kind (occupancy returns at end, consumable charges actual and returns remainder,
persistent returns only on reap); planted control: a consumable admitted past its balance must red.

P1 -- occupancy through the engine. Box OS, seats (with `seat_margin`), Slurm cpu/mem as providers; delete
`STATED_ONLY`, `submitted`, the duplicate seat readers, inline `Supply(` constructions in ledger/order,
forgetake/forgetry/forgetick admission. Architecture test (`NAMED-SETS-NOT-COUNTS`): the set of modules
that construct `Supply(` or call `admit(` is exactly `{lab_commons.resource.engine}` plus the named
providers' `measure`.

P2 -- consumables. Tokens (ai-lab), `core_h` and `submits` (Slurm), `forge_requests` (dev-lab). Delete
`ai_lab.budget` ledger, `daily_budget_core_h`/`over_budget`, `standing()` core-hours. The token JSONL is
not migrated (a day's window; it expires). Tests: a session whose cap exceeds the key balance is refused
before any request is sent; a provider report larger than the local ledger lowers the balance; two
concurrent sessions cannot together exceed the balance; N sub-agents admitted equals what the balance
admits (no concurrency constant exists -- census test greps for one).

P3 -- persistent. Disk/files/venvs/worktrees providers on workstations and Slurm homes; worktree and venv
creation declare a footprint claim persisted with a liveness probe; retention becomes reclaim only.
Tests: creation past quota refused naming `files (asked, free)`; a footprint whose owner died is reaped
and its supply returns; a live one is never reaped.

P4 -- learned for every name. `compute.learned` records the `Actual` of every kind (tokens per session
kind, core-hours per part, footprint per env) and serves the next demand; repo data holds only priors.
Test: every consumer `Demand(` call site is in a named set that reads `learned` or the repo prior table
(census), and a run's next demand reflects its last actual.

Done when the phase tests pass on each repo and the delete list is empty in `git grep`.
