"""The GitHub Actions workflows are RENDERED from TOML and in sync: no drift, no hand-written file.

`.github/workflows/` is an artefact of `[tool.dev_lab.ci]` in pyproject.toml over dev-lab's family base;
`python -m dev_lab.dev.ciworkflows render` writes it. A hand edit, or a TOML move without a render, reds here.
"""

from __future__ import annotations

from pathlib import Path

from dev_lab.dev import ciworkflows

ROOT = Path(__file__).resolve().parents[1]


def test_every_workflow_matches_its_render() -> None:
    assert ciworkflows.drift(ROOT) == []


def test_ci_runs_the_one_pr_lifecycle() -> None:
    jobs = set(ciworkflows.workflows(ROOT)['ci.yml']['jobs'])
    assert jobs == {'parts', 'part', 'absent-parts', 'security', 'review', 'merge-ready', 'promote'}
