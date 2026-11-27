"""A commit on the cluster's LOGIN node: :func:`fetch` it into the account's cache for a job's artifact.

A TEST run's tree is never made here: its prep job fetches, builds and collects on a compute node
(:mod:`lab_commons.hpc.prep`). A step that stops before any job is queued raises :class:`NotSubmitted` --
no test ran, so it is not a red run.
"""

from __future__ import annotations

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.layout import PACKS, REQUIRE_ROOT
from lab_commons.hpc.shell import RunSpec, fetch_script, needs_script, pack

__all__ = ['NotSubmitted', 'fetch']


class NotSubmitted(RuntimeError):
    """A run stopped before any test job was queued; no test ran, so it is not a red run.

    A missing need, an unfetchable commit, a failed build or an empty collection -- at submit, or read from
    the prep job's outputs by the gather that would have planned round 0. The CLI exits INCONCLUSIVE.
    """


def fetch(spec: RunSpec, run: Runner, where: str) -> None:
    """Check *where*'s login-node needs, then make its bare cache hold *spec*'s commit (shipping a pack if asked).

    The first step of a job's artifact (:func:`lab_commons.hpc.artifact.build_artifact`).
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
        run(f'{REQUIRE_ROOT} && base64 -d > {PACKS}/{spec.sha}.pack', pack(spec.pack_from, spec.sha))
        if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
            msg = f'commit {spec.sha} is still missing on {where} after the pack was indexed'
            raise NotSubmitted(msg)
