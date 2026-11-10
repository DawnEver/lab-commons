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
    # 'head-gates' gained 2026-11-08 with dev-lab #314 (the pre-push rows became forge head statuses);
    # 'merge-rejudge' 2026-11-10 with dev-lab #365 (the merge is judged again after the review posts).
    assert jobs == {
        'parts',
        'part',
        'absent-parts',
        'head-gates',
        'security',
        'review',
        'merge-ready',
        'merge-rejudge',
        'promote',
    }
