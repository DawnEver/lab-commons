"""A commit's TREE on the cluster: fetch it into ``~/ci/trees/<sha>`` and build its env there.

The first half of :func:`lab_commons.hpc.run.submit_run`: a test run needs the whole tree. A step that stops
before any job is queued raises :class:`NotSubmitted` -- no test ran, so it is not a red run.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.shell import (
    BUILD_FAILED,
    COLLECT_MODULE,
    ROOT,
    RunSpec,
    build_script,
    facts,
    fetch_script,
    needs_script,
    pack,
    parse_times,
)

__all__ = ['NotSubmitted', 'build_run', 'fetch']


class NotSubmitted(RuntimeError):
    """:func:`submit_run` stopped before any job was queued; no test ran, so it is not a red run.

    A missing need, an unfetchable commit, a failed build or an empty collection: the CLI exits INCONCLUSIVE.
    """


def fetch(spec: RunSpec, run: Runner, where: str) -> None:
    """Check *where*'s login-node needs, then make its bare cache hold *spec*'s commit (shipping a pack if asked).

    The shared first step of a test run's tree (:func:`build_run`) and a job's artifact
    (:func:`lab_commons.hpc.artifact.build_artifact`).
    """
    said = run(needs_script(spec), None).splitlines()
    lacking = [line.removeprefix('@@@ missing ') for line in said if line.startswith('@@@ missing ')]
    if lacking:
        msg = f'{where} lacks {", ".join(lacking)} on its login node; nothing was fetched or built'
        raise NotSubmitted(msg)
    if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
        if spec.pack_from is None:
            msg = f'commit {spec.sha} is not on {spec.repo_url}; pass a local repository to pack it from'
            raise NotSubmitted(msg)
        run(f'base64 -d > {ROOT}/packs/{spec.sha}.pack', pack(spec.pack_from, spec.sha))
        if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
            msg = f'commit {spec.sha} is still missing on {where} after the pack was indexed'
            raise NotSubmitted(msg)


def build_run(spec: RunSpec, run: Runner, where: str) -> dict[str, Any]:
    """Fetch *spec*'s commit into ``~/ci/trees/<sha>`` and build its env there; its facts, plus ``prep`` times.

    The first half of :func:`lab_commons.hpc.run.submit_run`: a TEST run needs the whole tree. A job needs
    only a runnable artifact, never this tree (:mod:`lab_commons.hpc.artifact`). *where* names the login
    node in a refusal.
    """
    shipping = time.monotonic()
    fetch(spec, run, where)
    here = Path(__file__).parent
    run(f'cat > {ROOT}/bin/{COLLECT_MODULE}.py', (here / 'collect.py').read_text(encoding='utf-8'))
    shipped = round(time.monotonic() - shipping)
    built = run(build_script(spec), (here / 'pytest_item.py').read_text(encoding='utf-8'))
    if BUILD_FAILED in built:
        tail = built.split(BUILD_FAILED, 1)[1].strip()
        msg = f'the build for {spec.sha[:12]} failed on {where}, nothing was submitted; {tail}'
        raise NotSubmitted(msg)
    told: dict[str, Any] = dict(facts(built))
    if not told.get('env'):
        msg = (
            f'the venv built for {spec.sha[:12]} on {where} reported no env hash (lab_commons.envkey is not '
            'importable there), so its part could never be carried; nothing was submitted'
        )
        raise NotSubmitted(msg)
    return {**told, 'prep': {'ship': shipped, **parse_times(built)}}
