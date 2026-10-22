# lab-commons

The family's shared runtime and execution layer. Python package `lab_commons`:

- Runtime: `units`, `paths`, `file_io`, `log`, `structured`, `config`, `credentials`, `outcomes`, `viz`.
- Execution: `box` (one machine ledger), `compute` (units, results, executors), `hpc` (cluster tier),
  `supervise` (process supervision), and their helpers (`proc`, `liveness`, `resources`, `envkey`, ...).
- `dev` (forge, hooks, gate, verify) is moving to dev-lab; nothing outside `dev` may import it
  (`tests/test_arch_the_staying_side_never_imports_dev.py`).

Where things live:

- Hard rules: `.claude/rules/`; the family rule registry: `lab_commons.dev.rules`.
- Mechanism and how-to: `docs-src/dev/` (start at `index.md`); how this repo integrates: `docs-src/dev/integration.md`.
- Refusals and their remedies: `docs-src/dev/refusals.md`.

Doors:

- Verify what you touched: `.venv/Scripts/python.exe -m lab_commons.dev.verify -- <paths>`.
- Network verbs (push, fetch) retry then report: `lab_commons.dev.netverb`.
