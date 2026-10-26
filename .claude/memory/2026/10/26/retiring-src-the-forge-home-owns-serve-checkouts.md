---
name: retiring-src-the-forge-home-owns-serve-checkouts
description: User ruling 2026-10-26 retires ~/src -- G's serve checkouts move to the forge home (%LOCALAPPDATA%\lab-forge\repos); what was rescued, the PRs in flight, and why G is blocked
created: 2026-10-26
accessed: 2026-10-26
tags: [integrator, forge, serve, workstation, src]
metadata: {}
---

# Retiring ~/src: the forge home owns the serve checkouts

## Ruling (user, 2026-10-26)

- `~/src` is cleaned out entirely; every repo the user works on is in `Documents/PEMC`. "Build a mechanism":
  the serve checkouts live in the platform data dir, `%LOCALAPPDATA%\lab-forge\repos\<repo>` (XDG data on
  POSIX), never in a dev checkout. Closes handoff gap 3 (the user confirmed the relocation).

## Why ~/src existed

- `dev_lab.dev.workstation deploy --root` defaulted to `~/src`; `[serve] root = "C:\Users\linxu\src"` in the
  machine file. The `lab-forge-serve` Task's action had drifted to `~/src/lab-commons/.venv/pythonw.exe`
  (a lab-commons dev venv as the kit) -> ImportError `declared_seats` every tick; G has posted no tick since
  10-22 (gap A4). Sessions also edited in `~/src`, so it held unique work.

## Rescued before removal

- Unpushed branches fetched into the PEMC clones as `src-rescue/<branch>` (dev-lab, lab-commons, optimi-lab,
  wdg-lab); `pr/adopt-security-check` abandoned per the handoff. dev-lab cloned to `Documents/PEMC/dev-lab`
  (PEMC had none); its scratch P0 baseline files copied.
- 21 `output/logs` trees (mains + worktrees) copied with no-clobber into `PEMC/<repo>/output/logs`.
- Uncommitted integrator rows 124-126 committed here (e149610).
- Line-ending-only diffs, the stale `components/numerics-lab` submodule leftover, rust-toolchain CRLF: dropped.

## In flight

- dev-lab #453 (844470c): one `forge_home()`; `--root` and `[serve] root` retired by name; Task action and
  serve interpreter must be `<home>/repos/dev-lab`; planted-control arch test refuses `home()/'src'`. 95 passed.
- lab-commons #464: relock dev-lab 7ef2cd9 (handoff section 6) + these rows. base and security success;
  review errored (only G has `[capabilities.ai_lab]`).
- wdg-lab #392: same relock; local verify inconclusive (connection checkout absent, box memory exhausted).
- motronics #4943: `[tool.dev_lab.security]` exclude `attic`, ignore `S311` (the red on integrate/main
  dff9324515: 28 attic + 4 S311). Per-site noqa is refused by the repo ruff (RUF100). Open question for
  dev-lab: the table is read from integrate/main, so the first exemption PR is judged by the table it adds.
- Motronics relock is the forge's (`dev_lab.dev.relock`), not a hand job.

## Blocked, and the remaining steps

- A one-off serve with the integrator venv as kit was killed by the harness for low memory (4 GB free).
- Next: #453 merges -> `workstation deploy` (clones into the forge home, drops `[serve] root`, re-registers
  the Task) -> delete `~/src` (after checking no live process holds it).
