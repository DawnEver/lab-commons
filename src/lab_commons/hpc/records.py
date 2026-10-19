"""A run's RESULTS: streams folded into test cells, and the record ``run-<sha>-<part>-<req>.json.gz``.

A run's ``cells`` map each id it COVERED to its plain test cell (:class:`lab_commons.hpc.testcells.TestCell`);
an id it hands to another part is in ``handed`` and nowhere else. A run's state (``state.json``) is LIVE
-- a run in flight crosses any change -- so a state still holding the retired ``outcomes`` words is upgraded
on read (:func:`upgraded_state`). A run RECORD is regenerable: one in the retired shape is refused by name
(:func:`read_record`) and the forge re-produces its sha.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

from lab_commons.compute.learned import Measured, Run
from lab_commons.hpc.cluster import Runner, Unreachable
from lab_commons.hpc.grants import Machine
from lab_commons.hpc.pytest_item import MANY_FILE_HEADER, read_stream, split_streams, tagged
from lab_commons.hpc.shell import ROOT
from lab_commons.hpc.testcells import TestCell, counts, upgraded
from lab_commons.outcomes import Cell, Reason, Status

__all__ = [
    'RetiredRecord',
    'cells_of',
    'files_made',
    'fold',
    'held_cells',
    'job_measured',
    'kept_text',
    'read_record',
    'read_streams',
    'record_name',
    'record_of',
    'run_shas',
    'split_streams',
    'stream_sources',
    'summary',
    'upgraded_state',
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
    'resolved',
    'cluster',
    'python',
    'prep',
    'rounds',
    'cells',
    'handed',
    'carried',
)

#: The key the retired record and state shapes held their per-id words under.
_RETIRED_KEY: Final = 'outcomes'


class RetiredRecord(ValueError):
    """A run record in the retired word shape: regenerable, so refused -- the forge re-produces its sha."""


def _plain(line: dict[str, Any]) -> dict[str, Any]:
    """A stream's plain test cell, a retired ``{"outcome", "why"}`` line upgraded (a round in flight)."""
    one = TestCell.from_plain(line) if 'status' in line else upgraded(str(line['outcome']), str(line.get('why', '')))
    if one is None:
        msg = f'a stream line reads as no cell: {line!r}'
        raise ValueError(msg)
    return one.to_plain()


def _cell(status: Status, kind: str, text: str) -> dict[str, Any]:
    return TestCell(Cell(status, reason=Reason(kind, text))).to_plain()


def fold(
    items: list[dict[str, Any]],
    streams: dict[int, str],
    cells: dict[str, dict[str, Any]],
    measured: Measured,
    *,
    shards: Sequence[Sequence[int]],
    walls: Sequence[float],
    alone: Mapping[int, int] | None = None,
) -> tuple[list[list[str]], bool, float]:
    """Record one round's streams; return the unfinished ids, halved, whether an item was KILLED, and busy seconds.

    *busy* is the sum of every closed item's own wall -- what the processes used of the cores charged.

    An item that EXITED (``done`` >= 0) before reporting every id names the id it was running: its last
    START line, when that id has no cell. It is ``ERROR`` with the exit as its reason, and only the ids
    after it are re-queued; with no unreported START to name it (a file that never imported, an id no longer
    collected), the unreported ids are ``PENDING``: never reported.

    An item closed by a signal (``done`` < 0: the cgroup's OOM killer, a timeout) did not finish its ids;
    they are retried, halved, like an item that never closed -- never stamped ``PENDING``.

    AN OUT_OF_MEMORY ITEM IS BISECTED, AND A LONE HOG IS ITS OWN CELL. *alone* maps each item killed
    OUT_OF_MEMORY at the most memory any grant gives (:func:`lab_commons.hpc.plan.grantable_gb`) to that
    limit in MB. Halving it again isolates the test that blows the memory; once one unfinished id is
    left, no grant can give it more, so it is ``OUT_OF_MEMORY`` by id with its peak and the limit as its
    reason -- never ``REJECTED``: the machine, not the test, said no -- and every other id of the item got
    a real result on the way down.

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
        cells.update({node: _plain(line) for node, line in folded['cells'].items()})
        measured.learn(group, folded)
        rest = [n for n in group if n not in folded['cells']]
        signalled = folded['done'] is not None and int(folded['done'].get('done', 0)) < 0
        killed = killed or signalled
        if folded['done'] is not None and not signalled:
            hit = folded['started']
            if hit not in rest:
                exited = f'the item exited {folded["done"]["done"]} without reporting it'
                cells.update(dict.fromkeys(rest, _cell(Status.PENDING, 'unreported', exited)))
                continue
            why = f'the item exited {folded["done"]["done"]} while this test ran; the ids after it re-run'
            cells[hit] = _cell(Status.ERROR, 'exit', why)
            after = [n for n in rest if n != hit]
            pending += [after] if after else []
            continue
        measured.killed(group, folded, spent.get(index, 0.0))
        if len(rest) == 1 and index in limits:
            peak = (folded['done'] or {}).get('peak_mb')
            cells[rest[0]] = _cell(
                Status.OUT_OF_MEMORY,
                'out_of_memory',
                f'alone at {limits[index]} MB, the most any grant gives'
                f' (peak {"unread" if peak is None else f"{float(peak):.0f} MB"})',
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


def stream_sources(sha: str, tag: str) -> tuple[str, str]:
    """The ONE stream file of *sha*'s round *tag* in its tree, and the ONE file retention keeps per run.

    A superseded run's finished shards are carried (:func:`held_cells`) long after its tree is garbage, so
    retention keeps the round's stream beside the run state (:func:`kept_text`) before the tree goes
    (:mod:`lab_commons.dev.retentionplan`): a file is an HPC resource.
    """
    return f'{ROOT}/trees/{sha}/.lab-ci/{tag}.jsonl', f'{ROOT}/runs/{sha}/streams.jsonl'


def _retired(live: str, kept: str, tag: str) -> tuple[str, str]:
    """Where the many-file shape put a round's streams: a directory per round, and a kept directory per run."""
    return live.removesuffix('.jsonl'), f'{kept.removesuffix(".jsonl")}/{tag}.txt'


def kept_text(live: Path) -> str:
    """What retention keeps of the round streaming to *live*: a ``round`` header and its tagged lines.

    The round's retired directory beside *live* (a round in flight across the change) is upgraded into it;
    a retired kept file ``<tag>.txt`` passed as *live* is upgraded alone. Empty when nothing was streamed.
    """
    tag = live.stem
    texts = [live.read_text(encoding='utf-8')] if live.is_file() else []
    if (old := live.with_suffix('')).is_dir():
        texts[:0] = [
            f'{MANY_FILE_HEADER}{p.name}\n{p.read_text(encoding="utf-8")}\n' for p in sorted(old.glob('*.jsonl'))
        ]
    lines = [json.dumps(line) for text in texts for line in tagged(text) if 'round' not in line]
    return '\n'.join([json.dumps({'round': tag}), *lines]) + '\n' if lines else ''


def read_streams(run: Runner, live: str, kept: str) -> dict[int, str]:
    """Every item stream of a round in one command: its tree's file while the tree exists, else the kept file.

    FORMATS-UPGRADE-ON-READ: a round in flight across the change also has its retired directory read, and a
    run kept before it its retired ``streams/<tag>.txt``; both are upgraded by :func:`tagged`.
    """
    tag = PurePosixPath(live).stem
    old_live, old_kept = _retired(live, kept, tag)
    listing = (
        f'for f in {old_live}/*.jsonl; do [ -e "$f" ] && echo "{MANY_FILE_HEADER}${{f##*/}}" && cat "$f" && echo; done'
    )
    out = run(
        f'if [ -e {live} ] || [ -d {old_live} ]; then {listing}; cat {live} 2>/dev/null; '
        f'elif [ -e {kept} ]; then cat {kept}; else cat {old_kept} 2>/dev/null; fi; true',
        None,
    )
    return split_streams(out, tag)


def cells_of(record: dict[str, Any]) -> dict[str, TestCell]:
    """``{id: its test cell}`` over the ids *record* covered."""
    return {node: TestCell.from_plain(plain) for node, plain in record['cells'].items()}


def summary(record: dict[str, Any]) -> str:
    """``ok=…, rejected=…`` counts of a record's cells, by status (an xfail as ``rejected/expected-rejected``)."""
    return ', '.join(f'{k}={v}' for k, v in sorted(counts(cells_of(record).values()).items()))


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
    """The record :func:`write_record` wrote; one in the retired word shape raises :class:`RetiredRecord`."""
    record = json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
    if 'cells' not in record:
        msg = (
            f'{path.name} is a run record of the retired word shape ({_RETIRED_KEY}: passed/failed/...); a record '
            'is regenerable, so it is refused, never upgraded -- the forge re-produces its sha'
        )
        raise RetiredRecord(msg)
    return record


def job_measured(
    rounds: Sequence[dict[str, Any]], nodes: Iterable[str], measured: Measured, *, cpus: int, files: float = 0.0
) -> Run:
    """What a whole Slurm run measured, as one :class:`~lab_commons.compute.learned.Run`.

    Its peak is one TASK's (the worst file it ran, as :meth:`Measured.worker_gb` prices it, unpadded);
    its cpu-seconds are every shard's elapsed times the *cpus* each held; its wall is the rounds' makespans;
    its *files* what :func:`files_made` counted.
    """
    walls = [r.get('walls_s') or [0.0] for r in rounds]
    return Run(
        peak_mem_gb=measured.peak_gb(nodes),
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


def run_shas(machine: Machine, runners: dict[str, Runner], part: str) -> list[str]:
    """Every sha some grant holds a run of *part* of (``~/ci/runs/<sha>/<part>/state.json``); unreachable holds none."""
    found: set[str] = set()
    for grant in machine.grants:
        try:
            listing = runners[grant.account](f'ls {ROOT}/runs/*/{part}/state.json 2>/dev/null || true', None)
        except Unreachable:
            continue
        found |= {line.split('/')[-3] for line in listing.split() if line.endswith(f'/{part}/state.json')}
    return sorted(found)


def held_cells(state: dict[str, Any], run: Runner) -> dict[str, tuple[dict[str, Any], str]]:
    """``{node: (plain test cell, origin sha)}`` of every DECIDED cell a run's *state* holds, superseded shards too.

    Its record when it has one, else its folded rounds plus every id the streams of its round in flight
    already reported -- the finished shards of a SUPERSEDED run. A RETRYABLE cell (never reported, lost, killed)
    is no answer and is never carried. *state* is LIVE and is read through :func:`upgraded_state`.

    A cell carried into it keeps its origin; one it ran names its own sha.
    """
    state = upgraded_state(state)
    record = state.get('record')
    cells = dict((record or state)['cells'])
    current = state.get('current')
    if record is None and current:
        streams = read_streams(run, *stream_sources(state['sha'], current['tag']))
        for index, item in enumerate(current['items']):
            found = read_stream(streams.get(index, ''), item['ids'])['cells']
            cells.update({node: _plain(line) for node, line in found.items()})
    origin = (record or state).get('carried', {})
    return {
        node: (plain, origin.get(node, state['sha']))
        for node, plain in cells.items()
        if '::' in node and TestCell.from_plain(plain).status.decided
    }


def _upgraded_words(holder: dict[str, Any]) -> dict[str, Any]:
    """*holder* with its retired ``outcomes`` words (and ``reasons``) read as ``cells``; a handed id is dropped."""
    if _RETIRED_KEY not in holder:
        return holder
    reasons = holder.get('reasons', {})
    rest = {k: v for k, v in holder.items() if k not in (_RETIRED_KEY, 'reasons')}
    read = {node: upgraded(word, reasons.get(node, '')) for node, word in holder[_RETIRED_KEY].items()}
    cells = {node: one.to_plain() for node, one in read.items() if one is not None}
    return {**rest, 'cells': cells}


def _upgraded_arrays(state: dict[str, Any]) -> dict[str, Any]:
    """A state of ONE array per round (``job_id``, a run-wide ``oom``) read as one array per memory class.

    Before 2026-10-19 a round was one array at one memory and an OOM doubled every later item's
    (:mod:`lab_commons.hpc.run`); a run in flight across the change keeps its round as one band.
    """
    rounds = [
        {**{k: v for k, v in r.items() if k != 'job_id'}, 'job_ids': [r['job_id']]} if 'job_id' in r else r
        for r in state.get('rounds', [])
    ]
    current = state.get('current')
    if 'oom' in state:  # the run-wide growth lands on the files of the round in flight, the ones it would size
        files = {n.split('::', maxsplit=1)[0] for item in (current or {}).get('items', ()) for n in item['ids']}
        state = {**state, 'grown': {name: float(state['oom']) for name in files if state['oom'] > 1}}
    state = {**{k: v for k, v in state.items() if k != 'oom'}, 'rounds': rounds}
    state.setdefault('grown', {})
    if current and 'job_id' in current:
        last = rounds[-1]
        band = {
            'shards': [0, current['shards']],
            'mem_mb': last['mem_mb'],
            'minutes': last['minutes'],
            'throttle': last['throttle'],
            'at_ceiling': current.get('at_ceiling'),
            'job_id': current['job_id'],
        }
        rest = {k: v for k, v in current.items() if k not in ('job_id', 'at_ceiling')}
        state['current'] = {
            **rest,
            'job_ids': [current['job_id']],
            'bands': [band],
            'ceiling': current.get('at_ceiling') is not None,
        }
    return state


def upgraded_state(state: dict[str, Any]) -> dict[str, Any]:
    """A run's LIVE *state*, its retired word and array shapes (and its record's words) upgraded on read."""
    state = _upgraded_arrays(_upgraded_words(state))
    if isinstance(state.get('record'), dict):
        state = {**state, 'record': _upgraded_words(state['record'])}
    return state


def record_of(state: dict[str, Any], account: str) -> dict[str, Any]:
    """The run record of a finished *state*; ``carried`` names each cell another run's sha ran."""
    return {
        **{key: state.get(key, {}) for key in _STATE_KEYS},
        'req': state['stamp'],
        'account': account,
        'job_ids': [job for r in state['rounds'] for job in r['job_ids']],
        'plan': state['rounds'][0] if state['rounds'] else {},
        **state['measured'],
    }
