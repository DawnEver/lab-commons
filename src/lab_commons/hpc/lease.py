"""LEASED TREES: the one place an ad-hoc per-commit tree lives on a cluster, and the reaper that bounds them.

An A/B comparison, a bisect or a census needs a checkout of a commit on the login node; a test run's tree
(``~/ci/trees/<sha>``, :mod:`lab_commons.hpc.tree`) is retention's and goes once its run is out of flight. Before
this door, lanes ran ``git worktree add ~/ab/cn/<sha>`` over ``ssh`` -- 69 trees and 2.3 M files that nothing
reaped (Ada 2026-11-05). A lease is the same worktree, of the SAME bare cache per repository
(:func:`lab_commons.hpc.shell.cache_dir`), with a record saying who holds it, why, and until when::

    ~/ci/leases/trees/<sha>/              the detached worktree (submodules included)
    ~/ci/leases/records/<sha>/<holder>.json   {sha, repo_url, holder, purpose, created, expires}
    ~/ci/leases/reaped/<yyyy-mm-dd>.jsonl     one line per removal: path, sha, why, when

THE RECORD IS WRITTEN BEFORE THE TREE, so a tree never exists without a lease naming it, and the reaper never
races an acquire. A tree is REAPED (:func:`reap_script`) once no holder's lease is unexpired AND no queued or
running Slurm job of this account works at or under it -- the guard ``clean`` uses, widened to queued jobs, since a
queued job will ``cd`` there. An expired record whose tree a job still uses is kept, and the reap line says so.

WHICH WORK USES WHICH. A tree used only INSIDE one Slurm job (a bisect step, a census probe that runs and
exits) is checked out into the node-local ``$TMPDIR`` from the bare cache, inside that job: the scheduler wipes
it at job end and it never touches the home quota. Anything that outlives one job -- a tree read from the login
node, shared by several jobs, or kept across a session (an A/B pair, a bisect driver) -- is a lease. Neither
ever lives at a home path the managed roots do not name (:data:`lab_commons.hpc.home.MANAGED`).

Every script here is a plain string run by a :data:`lab_commons.hpc.cluster.Runner`.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final

from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.shell import BUILD_FAILED, CACHES, ROOT, RunSpec, cache_dir, fetch_script, submodules_script

__all__ = [
    'LEASES',
    'MAX_TTL_HOURS',
    'Lease',
    'LeaseRefused',
    'Reaped',
    'acquire',
    'held',
    'reap',
    'release',
]

#: The lease root, under the managed ``~/ci``.
LEASES: Final = f'{ROOT}/leases'

#: A lease that never expires is ``~/ab`` again: every lease ends, and one may be renewed by acquiring again.
MAX_TTL_HOURS: Final = 7 * 24

#: A holder or purpose is one plain token: it becomes a file name and a log field.
_HOLDER: Final = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$')

_RECORD: Final = '@@@ record '
_TREE: Final = '@@@ tree '
_JOB: Final = '@@@ job '
_REAPED: Final = '@@@ reaped '
_LEFT: Final = '@@@ left '

#: How a reap line names each outcome.
_VERB: Final = {'gone': 'reaped', 'kept': 'kept', 'left': 'LEFT'}


class LeaseRefused(ValueError):
    """A lease was refused before anything was written; the message names why."""


@dataclass(frozen=True)
class Lease:
    """One holder's claim on one commit's tree, until ``expires`` (epoch seconds)."""

    sha: str
    repo_url: str
    holder: str
    purpose: str
    created: int
    expires: int

    def live(self, now: float) -> bool:
        """Whether the lease still holds at *now*."""
        return self.expires > now

    def text(self, now: float) -> str:
        """``<sha12> <holder> <purpose> expires <iso> (live|EXPIRED)``."""
        when = datetime.fromtimestamp(self.expires, UTC).strftime('%Y-%m-%dT%H:%MZ')
        return (
            f'{self.sha[:12]} {self.holder} {self.purpose!r} expires {when} ({"live" if self.live(now) else "EXPIRED"})'
        )


@dataclass(frozen=True)
class Reaped:
    """One tree the reaper judged: ``gone``, ``kept`` (a job works under it) or ``left`` (removal failed)."""

    sha: str
    path: str
    why: str
    outcome: str

    @property
    def gone(self) -> bool:
        """Whether the tree was removed."""
        return self.outcome == 'gone'

    def text(self) -> str:
        """``reaped|kept|LEFT <path> (<why>)``."""
        return f'{_VERB[self.outcome]} {self.path} ({self.why})'


def _checked(holder: str, purpose: str, ttl_hours: float) -> None:
    if not _HOLDER.fullmatch(holder):
        msg = f'a holder is one plain token (letters, digits, ._@-), got {holder!r}'
        raise LeaseRefused(msg)
    if not purpose.strip() or '\n' in purpose:
        msg = 'a lease names its purpose on one line (--purpose "bisect of ...")'
        raise LeaseRefused(msg)
    if not 0 < ttl_hours <= MAX_TTL_HOURS:
        msg = f'a lease ends: --ttl is (0, {MAX_TTL_HOURS}] hours, got {ttl_hours}; renew by acquiring again'
        raise LeaseRefused(msg)


def acquire(run: Runner, repo_url: str, sha: str, *, holder: str, purpose: str, ttl_hours: float, now: float) -> str:
    """Lease *sha*'s tree for *holder* until ``now + ttl``; returns the tree's absolute path on the cluster.

    Fetches the commit into the repository's one bare cache, writes the lease record, THEN adds the worktree
    (reused when present: a second holder or a renewal only writes its record). Refused by name for a bad
    holder, purpose or ttl, or a commit the remote does not have.
    """
    spec = RunSpec(sha=sha, repo_url=repo_url)  # refuses anything but a full 40-hex id
    _checked(holder, purpose, ttl_hours)
    if run(fetch_script(spec), None).strip().splitlines()[-1:] != ['have']:
        msg = f'commit {sha} is not on {repo_url}; push it first (nothing was leased)'
        raise LeaseRefused(msg)
    lease = Lease(sha, repo_url, holder, purpose.strip(), int(now), int(now + ttl_hours * 3600))
    tree, records = f'{LEASES}/trees/{sha}', f'{LEASES}/records/{sha}'
    script = '\n'.join(
        [
            'set -eo pipefail',
            f'mkdir -p {records} {LEASES}/trees',
            f'cat > {records}/{holder}.json',
            f'[ -d {tree} ] || git -C {cache_dir(repo_url)} worktree add -q --detach {tree} {sha}',
            f'cd {tree}',
            *submodules_script(repo_url),
            'pwd -P',
        ]
    )
    said = run(script, json.dumps(asdict(lease))).strip().splitlines()
    if not said or BUILD_FAILED in '\n'.join(said):
        msg = f'the tree of {sha[:12]} could not be checked out: {" ".join(said)[-400:]}'
        raise LeaseRefused(msg)
    return said[-1]


def release(run: Runner, sha: str, holder: str) -> None:
    """Drop *holder*'s lease on *sha*; the tree goes at the next reap once no lease holds it."""
    RunSpec(sha=sha, repo_url='-')
    if not _HOLDER.fullmatch(holder):
        msg = f'a holder is one plain token, got {holder!r}'
        raise LeaseRefused(msg)
    run(f'rm -f -- {LEASES}/records/{sha}/{shlex.quote(holder)}.json', None)


#: Lists every lease record, every leased tree and every queued or running job's work directory.
_STATE: Final = (
    f'L={LEASES}; '
    f'for f in "$L"/records/*/*.json; do [ -f "$f" ] && printf "{_RECORD}%s\\n" "$(tr -d "\\n" < "$f")"; done; '
    f'for t in "$L"/trees/*; do [ -d "$t" ] && printf "{_TREE}%s\\n" "$(cd "$t" && pwd -P)"; done; '
    f'squeue -h -u "$(id -un)" -o %Z 2>/dev/null | sed "s|^|{_JOB}|"; true'
)


def _state(run: Runner) -> tuple[list[Lease], list[str], list[str]]:
    leases, trees, jobs = [], [], []
    for line in run(_STATE, None).splitlines():
        if line.startswith(_RECORD):
            try:
                raw: dict[str, Any] = json.loads(line.removeprefix(_RECORD))
                leases.append(Lease(**{k: raw[k] for k in Lease.__dataclass_fields__}))
            except (ValueError, KeyError, TypeError):
                continue  # an unreadable record holds nothing; its tree is judged by the job guard alone
        elif line.startswith(_TREE):
            trees.append(line.removeprefix(_TREE).rstrip('/'))
        elif line.startswith(_JOB):
            jobs.append(line.removeprefix(_JOB).strip().rstrip('/'))
    return leases, trees, jobs


def held(run: Runner) -> list[Lease]:
    """Every lease record on the cluster, oldest first."""
    return sorted(_state(run)[0], key=lambda lease: (lease.created, lease.sha, lease.holder))


def _under(path: str, jobs: list[str]) -> list[str]:
    return [d for d in jobs if d == path or d.startswith(path + '/')]


def reap(run: Runner, *, now: float) -> list[Reaped]:
    """Remove every leased tree no unexpired lease and no queued or running job holds; log each to a dated record.

    The tree, its records and the cache's worktree entry go together; each removal is verified and appended to
    ``~/ci/leases/reaped/<yyyy-mm-dd>.jsonl`` ON THE CLUSTER. A tree a job still uses is kept, its expiry noted.
    """
    leases, trees, jobs = _state(run)
    by_sha: dict[str, list[Lease]] = {}
    for lease in leases:
        by_sha.setdefault(lease.sha, []).append(lease)
    done: list[Reaped] = []
    for tree in trees:
        sha = tree.rsplit('/', 1)[-1]
        mine = by_sha.get(sha, [])
        if any(lease.live(now) for lease in mine):
            continue
        if busy := _under(tree, jobs):
            done.append(Reaped(sha, tree, f'lease expired but a job works under it ({busy[0]})', 'kept'))
            continue
        why = 'every lease expired' if mine else 'no lease names it'
        done.append(_remove(run, sha, tree, why, now))
    for sha, mine in by_sha.items():  # a record whose tree is already gone
        if not any(t.rsplit('/', 1)[-1] == sha for t in trees) and not any(m.live(now) for m in mine):
            run(f'rm -rf -- {LEASES}/records/{sha}', None)
    return done


def _remove(run: Runner, sha: str, tree: str, why: str, now: float) -> Reaped:
    day = datetime.fromtimestamp(now, UTC).strftime('%Y-%m-%d')
    line = json.dumps({'path': tree, 'sha': sha, 'why': why, 'when': int(now)})
    said = run(
        '\n'.join(
            [
                f'T={shlex.quote(tree)}; L={LEASES}',
                'R=$(cd "$L/trees" && pwd -P)',
                f'case "$T" in "$R"/*) ;; *) echo "{_LEFT}outside the lease root"; exit 0;; esac',
                f'rm -rf -- "$T" "$L/records/{sha}"',
                f'for c in {CACHES}/*.git; do [ -d "$c" ] && git -C "$c" worktree prune 2>/dev/null; done',
                f'if [ -e "$T" ]; then echo "{_LEFT}$T"; exit 0; fi',
                f'mkdir -p "$L/reaped" && cat >> "$L/reaped/{day}.jsonl" && echo "{_REAPED}$T"',
            ]
        ),
        line + '\n',
    )
    gone = any(s.startswith(_REAPED) for s in said.splitlines())
    return Reaped(sha, tree, why, 'gone' if gone else 'left')
