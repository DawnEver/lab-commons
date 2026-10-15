"""A run's RESULTS: streams folded into outcomes, and the record ``run-<sha>-<part>-<req>.json.gz``."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from lab_commons.compute.learned import Measured, Run
from lab_commons.hpc.cluster import Runner, Unreachable
from lab_commons.hpc.grants import Machine
from lab_commons.hpc.pytest_item import read_stream
from lab_commons.hpc.shell import ROOT

__all__ = [
    'KEPT_STREAMS',
    'files_made',
    'fold',
    'held_outcomes',
    'job_measured',
    'read_record',
    'read_streams',
    'record_name',
    'record_of',
    'run_shas',
    'stream_dirs',
    'summary',
    'write_record',
]

#: State keys the record carries as they are.
_STATE_KEYS: Final = (
    'sha',
    'repo',
    'scope',
    'platform',
    'system',
    'env',
    'cluster',
    'python',
    'prep',
    'rounds',
    'outcomes',
    'handed',
    'carried',
)

#: Outcomes that are not a run's own: never reported, or handed to another part.
_NOT_RUN: Final = frozenset({'lost', 'missing', 'not-covered'})


def fold(
    items: list[dict[str, Any]],
    streams: dict[int, str],
    outcomes: dict[str, str],
    measured: Measured,
    reasons: dict[str, str],
    *,
    shards: Sequence[Sequence[int]],
    walls: Sequence[float],
    alone: Mapping[int, int] | None = None,
) -> tuple[list[list[str]], bool, float]:
    """Record one round's streams; return the unfinished ids, halved, whether an item was KILLED, and busy seconds.

    *busy* is the sum of every closed item's own wall -- what the processes used of the cores charged.

    An item that EXITED (``done`` >= 0) before reporting every id names the id it was running: its last
    START line, when that id has no outcome. It is ``failed`` with the exit as its reason, and only the ids
    after it are re-queued; with no unreported START to name it (a file that never imported, an id no longer
    collected), the unreported ids are ``missing``.

    An item closed by a signal (``done`` < 0: the cgroup's OOM killer, a timeout) did not finish its ids;
    they are retried, halved, like an item that never closed -- never stamped ``missing``.

    AN OUT_OF_MEMORY ITEM IS BISECTED, AND A LONE HOG IS ITS OWN FAILURE. *alone* maps each item killed
    OUT_OF_MEMORY at the most memory any grant gives (:func:`lab_commons.hpc.plan.grantable_gb`) to that
    limit in MB. Halving it again isolates the test that blows the memory; once one unfinished id is
    left, no grant can give it more, so it is ``failed`` by id with its peak and the limit as its reason,
    and every other id of the item got a real result on the way down.

    WHAT A KILL COST IS MEASURED TOO. A shard (*shards*: item ranges) runs its items back to back for
    the seconds Slurm says it ran (*walls*, one per shard), so the first item of a shard that never closed
    was killed after that minus what the items before it took; a signalled item says its own wall.
    Measured 2026-10-11: a shard killed after 7 s was charged its whole 216-min PLANNED wall, so one id
    carried a 12960 s floor it never ran. That time goes to
    :meth:`Measured.killed`, so the id the kill interrupted is never re-priced below it.
    """
    pending: list[list[str]] = []
    limits = alone or {}
    killed = False
    folds = [read_stream(streams.get(index, ''), item['ids']) for index, item in enumerate(items)]
    spent = _killed_after(folds, shards, walls)
    for index, (item, folded) in enumerate(zip(items, folds, strict=True)):
        group = item['ids']
        outcomes.update(folded['outcomes'])
        reasons.update(folded['whys'])
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['outcomes']]
        signalled = folded['done'] is not None and int(folded['done'].get('done', 0)) < 0
        killed = killed or signalled
        if folded['done'] is not None and not signalled:
            hit = folded['started']
            if hit not in rest:
                outcomes.update(dict.fromkeys(rest, 'missing'))
                continue
            outcomes[hit] = 'failed'
            reasons[hit] = f'the item exited {folded["done"]["done"]} while this test ran; the ids after it re-run'
            after = [n for n in rest if n != hit]
            pending += [after] if after else []
            continue
        measured.killed(group, folded, spent.get(index, 0.0))
        if len(rest) == 1 and index in limits:
            peak = (folded['done'] or {}).get('peak_mb')
            outcomes[rest[0]] = 'failed'
            reasons[rest[0]] = (
                f'OUT_OF_MEMORY alone at {limits[index]} MB, the most any grant gives'
                f' (peak {"unread" if peak is None else f"{float(peak):.0f} MB"})'
            )
            continue
        half = (len(rest) + 1) // 2
        pending += [part for part in (rest[:half], rest[half:]) if part]
    busy = sum(float(f['done']['wall']) for f in folds if f['done'] is not None)
    return pending, killed, busy


def _killed_after(
    folds: list[dict[str, Any]], shards: Sequence[Sequence[int]], walls: Sequence[float]
) -> dict[int, float]:
    """Seconds each killed item ran: its own ``done`` wall when signalled, else what its shard's wall had left."""
    spent: dict[int, float] = {}
    for (start, stop), wall in zip(shards, walls, strict=True):
        left = wall
        for index in range(start, stop):
            done = folds[index]['done']
            if done is None:
                spent[index] = max(0.0, left)
                break
            if int(done.get('done', 0)) < 0:
                spent[index] = float(done['wall'])
            left -= float(done['wall'])
    return spent


#: Where an unfinished run's streams are KEPT once retention removes its tree: ``runs/<sha>/<KEPT>/<tag>``.
KEPT_STREAMS: Final = 'streams'


def stream_dirs(sha: str, tag: str) -> tuple[str, str]:
    """The streams of *sha*'s round *tag*: in its tree, else where retention kept them (``runs/<sha>``).

    A superseded run's finished shards are carried (:func:`held_outcomes`) long after its tree is garbage, so
    retention moves ``.lab-ci/<tag>`` beside the run state before the tree goes
    (:mod:`lab_commons.dev.retentionplan`).
    """
    return f'{ROOT}/trees/{sha}/.lab-ci/{tag}', f'{ROOT}/runs/{sha}/{KEPT_STREAMS}/{tag}'


def read_streams(run: Runner, *directories: str) -> dict[int, str]:
    """Every item stream (``<index>.jsonl``) in the first of *directories* that exists -- one remote command."""
    listing = 'for f in *.jsonl; do [ -e "$f" ] && echo "@@@ $f" && cat "$f"; done'
    enter = ' || '.join(f'cd {directory} 2>/dev/null' for directory in directories)
    out = run(f'{{ {enter}; }} && {listing}; true', None)
    streams: dict[int, list[str]] = {}
    current: list[str] = []
    for line in out.splitlines():
        if line.startswith('@@@ ') and line.endswith('.jsonl'):
            current = streams.setdefault(int(line[4:-6]), [])
        else:
            current.append(line)
    return {index: '\n'.join(lines) for index, lines in streams.items()}


def summary(record: dict[str, Any]) -> str:
    """``passed=…, failed=…`` counts of a record, worst first."""
    counts: dict[str, int] = {}
    for outcome in record['outcomes'].values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return ', '.join(f'{k}={v}' for k, v in sorted(counts.items()))


def record_name(record: dict[str, Any]) -> str:
    """``run-<sha>-<part>-<req>.json.gz`` -- one run of one sha on one platform, one request."""
    return f'run-{record["sha"]}-{record["platform"]}-{record["req"]}.json.gz'


def write_record(record: dict[str, Any], directory: Path) -> Path:
    """The record gzipped under *directory* (created) at :func:`record_name`; returns its path."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / record_name(record)
    partial = target.with_name(target.name + '.partial')  # atomic: a crash never leaves half a record
    partial.write_bytes(gzip.compress(json.dumps(record, sort_keys=True).encode('utf-8'), mtime=0))
    partial.replace(target)
    return target


def read_record(path: Path) -> dict[str, Any]:
    """The record :func:`write_record` wrote."""
    return json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))


def job_measured(
    rounds: Sequence[dict[str, Any]], outcomes: Iterable[str], measured: Measured, *, cpus: int, files: float = 0.0
) -> Run:
    """What a whole Slurm run measured, as one :class:`~lab_commons.compute.learned.Run`.

    Its peak is one TASK's (the worst file it ran, as :meth:`Measured.worker_gb` prices it, unpadded);
    its cpu-seconds are every shard's elapsed times the *cpus* each held; its wall is the rounds' makespans;
    its *files* what :func:`files_made` counted.
    """
    walls = [r.get('walls_s') or [0.0] for r in rounds]
    return Run(
        peak_mem_gb=measured.worker_gb(outcomes, prior_gb=0.0, safety=1.0),
        cpu_s=sum(sum(w) for w in walls) * cpus,
        wall_s=max(1.0, sum(max(w) for w in walls)),
        files=files,
    )


def files_made(home: Runner, state: dict[str, Any]) -> float:
    """The files a run leaves in the home -- its tree, run directory and env, one ``find | wc -l``; 0 unread.

    The account's FILE quota is a supply admission compares (user ruling 2026-10-14), so a run's file cost
    is learned like its memory. 0 is "not measured": the demand then falls back to the prior, never to free.
    """
    paths = [f'{ROOT}/trees/{state["sha"]}', f'{ROOT}/runs/{state["sha"]}']
    if env := (state.get('builds') or {}).get('env'):
        paths.append(f'{ROOT}/envs/{env}')
    text = home(f'find {" ".join(paths)} -xdev 2>/dev/null | wc -l', None).strip()
    return float(text) if text.isdigit() else 0.0


def run_shas(machine: Machine, runners: dict[str, Runner]) -> list[str]:
    """Every sha some grant holds a run state of (``~/ci/runs/<sha>/state.json``); an unreachable grant holds none."""
    found: set[str] = set()
    for grant in machine.grants:
        try:
            listing = runners[grant.account](f'ls {ROOT}/runs/*/state.json 2>/dev/null || true', None)
        except Unreachable:
            continue
        found |= {line.rstrip('/').split('/')[-2] for line in listing.split() if line.endswith('/state.json')}
    return sorted(found)


def held_outcomes(state: dict[str, Any], run: Runner) -> dict[str, tuple[str, str]]:
    """``{node: (outcome, origin sha)}`` a run's *state* holds, its superseded shards included.

    Its record when it has one, else its folded rounds plus every id the streams of its round in flight
    already reported -- the finished shards of a SUPERSEDED run.

    An outcome carried into it keeps its origin; one it ran names its own sha.
    """
    record = state.get('record')
    outcomes = dict((record or state)['outcomes'])
    current = state.get('current')
    if record is None and current:
        streams = read_streams(run, *stream_dirs(state['sha'], current['tag']))
        for index, item in enumerate(current['items']):
            outcomes.update(read_stream(streams.get(index, ''), item['ids'])['outcomes'])
    origin = (record or state).get('carried', {})
    return {
        node: (outcome, origin.get(node, state['sha']))
        for node, outcome in outcomes.items()
        if '::' in node and outcome not in _NOT_RUN
    }


def record_of(state: dict[str, Any], account: str) -> dict[str, Any]:
    """The run record of a finished *state*; ``carried`` names each outcome another run's sha ran."""
    return {
        **{key: state.get(key, {}) for key in _STATE_KEYS},
        'req': state['stamp'],
        'account': account,
        'job_ids': [r['job_id'] for r in state['rounds']],
        'plan': state['rounds'][0] if state['rounds'] else {},
        'reasons': {n: why for n, why in state['reasons'].items() if state['outcomes'].get(n) in ('failed', 'error')},
        **state['measured'],
    }
