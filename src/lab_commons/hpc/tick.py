"""ONE CRON TICK of a served repo on a cluster account: ``python -m lab_commons.hpc tick <repo>``.

THE HOST REFRESHES, THE REPO SERVES (incident 2026-10-13): a repo served from its OWN venv cannot update
itself when the kit in that venv is too old to run -- the code that would update it is the code that
fails. So the cron line runs THIS, from the host's lab-commons checkout
(:func:`lab_commons.hpc.deploy_spec.tick_command`), and only then the repo's own ``forge serve``. Every
step is the deploy's own and keyed (:func:`lab_commons.hpc.deploy_steps.update` and ``build``), so an
unchanged tick only fetches.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lab_commons.config import config_path
from lab_commons.hpc.declared import OutdatedKit, RetiredTable
from lab_commons.hpc.deploy_spec import LAB_COMMONS, Deployment, Repo, checkout_of, load
from lab_commons.hpc.deploy_steps import Step, Tools, build, update
from lab_commons.log import emit

__all__ = ['tick']


def _refresh(step: Step, spec: Deployment, home: Path, repo: Repo) -> str:
    """Fetch and check out *repo*'s ref, then bring its venv in step (each build skipped while its key holds)."""
    if rc := update(step, spec, home, repo):
        return f'its checkout update exited {rc}'
    try:
        rc = build(step, spec, home, repo)
    except (RetiredTable, OutdatedKit) as why:
        return str(why)
    return f'its venv build exited {rc}' if rc else ''


def tick(
    name: str,
    *,
    home: Path,
    config: Path | None = None,
    tools: Tools | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """One cron tick of served repo *name*: THE HOST refreshes, then the repo's own ``forge serve`` runs.

    Runs from the host's lab-commons checkout, never the repo's venv (incident 2026-10-13: a repo venv
    whose old ``forge serve`` exited 2 every tick could never run the code that would update it). First
    lab-commons follows its declared ref, then *name* follows its own and its venv is rebuilt where its
    inputs or the served lab-commons HEAD moved (:func:`lab_commons.hpc.deploy_steps.build`). A kit that
    cannot move is reported and the repo serves with the kit it has; a repo that cannot is RELEASED this
    tick with its reason, exit 2, and its ``forge serve`` never runs on a stale venv.
    """
    spec = load(config or config_path())
    repo = next((one for one in spec.repos if one.name == name), None) if spec else None
    if spec is None or repo is None:
        emit(f'[tick] refused: {name} is not a served repo of [deploy] in {config or config_path()}', err=True)
        return 2
    step = Step(tools or Tools(), dry=False)
    lab = next(one for one in spec.repos if one.name == LAB_COMMONS)
    if lab != repo and (why := _refresh(step, spec, home, lab)):
        emit(f'[tick] lab-commons not refreshed; {name} serves with the kit it has: {why}', err=True)
    if why := _refresh(step, spec, home, repo):
        emit(f'[tick] {name} released, not served this tick: {why}', err=True)
        return 2
    checkout = checkout_of(spec, home, repo)
    argv = [str(checkout / '.venv' / 'bin' / 'python'), '-m', 'lab_commons.dev.forge', 'serve']
    return run(argv, cwd=checkout, check=False).returncode
