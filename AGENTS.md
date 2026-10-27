# lab-commons

The family's shared runtime and execution layer. Python package `lab_commons`:

- Runtime: `units`, `paths`, `file_io`, `log`, `structured`, `config`, `credentials`, `outcomes`, `viz`.
- Execution: `box` (one machine ledger), `compute` (units, results, executors), `hpc` (cluster tier),
  `supervise` (process supervision), and their helpers (`proc`, `liveness`, `resources`, `envkey`, ...).
- The development system (forge, hooks, gate, verify, HPC hosting) is dev-lab, a `dev` dependency group,
  never a runtime dependency; nothing here imports it (`tests/test_arch_the_staying_side_never_imports_dev.py`).

Where things live:

- Hard rules: `.claude/rules/`; the family rule registry: `dev_lab.dev.rules`.
- Mechanism, how-to, integration and refusals with their remedies: dev-lab's `docs-src/dev/` (start at `index.md`).
- Family project files (`.gitignore`, `.gitattributes`, `.rgignore`, `Makefile`): this repo's delta is
  `tests/_famconfig_delta.py`; re-render with `python -m dev_lab.dev.famfiles --deltas tests/_famconfig_delta.py`.
  Workflows: `[tool.dev_lab.ci]` in `pyproject.toml`, rendered by `python -m dev_lab.dev.ciworkflows render`.

Doors:

- Verify what you touched: `.venv/Scripts/python.exe -m dev_lab.dev.verify -- <paths>`.
- Network verbs (push, fetch) retry then report: `dev_lab.dev.netverb`.
