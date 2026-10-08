"""Talk to the cluster: probe it, submit a plan as one job array, read back states and results.

EVERYTHING GOES THROUGH A RUNNER. A :data:`Runner` takes one shell command (and optional stdin) and
returns its stdout; :func:`local_runner` runs it here (already on a login node), :func:`ssh_runner`
runs it on a login node from a workstation. Files reach the cluster as ``cat > path`` on stdin, so a
run needs no ``scp`` and no shared filesystem with the caller -- and a test drives every function below
with a fake runner and no cluster.

THE RUN DIRECTORY IS THE RECORD. ``<workdir>/.lab-hpc/<name>-<stamp>/`` holds the manifest (items and
shard ranges), the array script, one result file per shard and one log per task. Re-running a failed
shard re-reads the same manifest; nothing about a run lives only in the caller's memory.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.hpc.config import Config
from lab_commons.hpc.plan import Plan
from lab_commons.hpc.slurm import PROBE_COMMAND, Snapshot, parse_snapshot

__all__ = [
    'TERMINAL_OK',
    'Runner',
    'Submission',
    'gather',
    'local_runner',
    'probe',
    'render_script',
    'shard_states',
    'ssh_runner',
    'submit',
]

#: Run one shell command, feeding *stdin*, and return its stdout; raise on a non-zero exit.
type Runner = Callable[[str, str | None], str]

#: The only array-task state that means the shard's results file should exist.
TERMINAL_OK: Final = 'COMPLETED'

_TIMEOUT: Final = 300.0


def _completed(argv: Sequence[str], stdin: str | None) -> str:
    """Run *argv*; stdin goes as UTF-8 BYTES.

    BYTES, BECAUSE TEXT MODE REWRITES IT. On Windows a text-mode pipe turns every LF into CRLF, and the
    array script that reached the cluster that way was refused outright -- measured on a real cluster 2026-10-08:
    ``sbatch: error: Batch script contains DOS line breaks``. What the caller wrote is what the cluster
    gets.
    """
    done = subprocess.run(
        argv,
        input=None if stdin is None else stdin.encode('utf-8'),
        capture_output=True,
        timeout=_TIMEOUT,
        check=False,
    )
    out, err = done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace')
    if done.returncode != 0:
        msg = f'{argv[0]} exited {done.returncode}: {err.strip() or out.strip()}'
        raise RuntimeError(msg)
    return out


def local_runner(command: str, stdin: str | None = None) -> str:
    """Run *command* with ``bash -lc`` on this machine (a login node)."""
    return _completed(['bash', '-lc', command], stdin)


def ssh_runner(host: str) -> Runner:
    """A runner that executes on *host* over key-based, non-interactive ``ssh``."""

    def run(command: str, stdin: str | None = None) -> str:
        return _completed(['ssh', '-o', 'BatchMode=yes', host, f'bash -lc {shlex.quote(command)}'], stdin)

    return run


def runner_for(config: Config) -> Runner:
    """``ssh`` when the config names a host, the local shell when it does not."""
    return ssh_runner(config.cluster.host) if config.cluster.host else local_runner


def probe(run: Runner, account: str = '') -> Snapshot:
    """Read the cluster once -- see :mod:`lab_commons.hpc.slurm`."""
    return parse_snapshot(run(PROBE_COMMAND, None), account)


def _sh_path(path: str) -> str:
    """Quote a path for the shell while keeping a leading ``~`` expandable."""
    if path == '~' or path.startswith('~/'):
        return '"$HOME"' + (shlex.quote(path[1:]) if len(path) > 1 else '')
    return shlex.quote(path)


def render_script(plan: Plan, config: Config, run_dir: str) -> str:
    """The array task script. The array range itself is an ``sbatch`` argument, so a resubmit reuses this."""
    directives = [
        f'--job-name={config.job.name}',
        f'--partition={plan.partition}',
        f'--account={plan.account}' if plan.account else '',
        f'--qos={plan.qos}' if plan.qos else '',
        '--nodes=1',
        '--ntasks=1',
        f'--cpus-per-task={plan.cpus}',
        f'--mem={plan.mem_mb}M',
        f'--gres=gpu:{plan.gpus}' if plan.gpus else '',
        f'--time={plan.minutes}',
        f'--output={run_dir}/logs/%A_%a.out',
    ]
    lines = ['#!/bin/bash', *(f'#SBATCH {d}' for d in directives if d), 'set -eo pipefail']
    lines += [f'cd {_sh_path(config.job.workdir)}', *config.job.setup]
    lines += [
        (
            'export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK" OPENBLAS_NUM_THREADS="$SLURM_CPUS_PER_TASK" '
            'MKL_NUM_THREADS="$SLURM_CPUS_PER_TASK"'
        ),
        f'exec {config.job.python} -m lab_commons.hpc.worker {_sh_path(run_dir)}/manifest.json "$SLURM_ARRAY_TASK_ID"',
    ]
    return '\n'.join(lines) + '\n'


@dataclass(frozen=True)
class Submission:
    """Where a run lives on the cluster and which job array holds it."""

    run_dir: str
    job_id: str
    shards: int


def _array_spec(indices: Sequence[int], throttle: int) -> str:
    """Sorted shard indices as Slurm ranges -- ``0-99,104%24`` -- so a large array stays one short argument."""
    runs: list[list[int]] = []
    for index in indices:
        if runs and index == runs[-1][1] + 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    spec = ','.join(str(a) if a == b else f'{a}-{b}' for a, b in runs)
    return f'{spec}%{max(1, throttle)}'


def submit(
    run: Runner,
    plan: Plan,
    config: Config,
    items: list[Any],
    *,
    stamp: str,
    only: Sequence[int] | None = None,
) -> Submission:
    """Write the manifest and script into a fresh run directory and submit the array.

    *only* resubmits chosen shard indices of an EXISTING run: the manifest and script are reused as
    written, so a retry runs exactly what the first attempt ran.
    """
    run_dir = f'{config.job.workdir.rstrip("/")}/.lab-hpc/{config.job.name}-{stamp}'
    quoted = _sh_path(run_dir)
    if only is None:
        manifest = {'entry': config.job.entry, 'items': items, 'shards': [list(s) for s in plan.shards]}
        run(f'mkdir -p {quoted}/logs {quoted}/results && cat > {quoted}/manifest.json', json.dumps(manifest))
        run(f'cat > {quoted}/job.sh', render_script(plan, config, run_dir))
    indices = list(range(len(plan.shards))) if only is None else sorted(only)
    if not indices:
        msg = 'no shard to submit'
        raise ValueError(msg)
    out = run(f'cd {quoted} && sbatch --parsable --array={_array_spec(indices, plan.throttle)} job.sh', None)
    job_id = out.strip().split(';')[0]
    if not job_id.isdigit():
        msg = f'sbatch did not return a job id: {out!r}'
        raise RuntimeError(msg)
    return Submission(run_dir=run_dir, job_id=job_id, shards=len(plan.shards))


_TASK: Final = re.compile(r'^(\d+)_(\d+|\[[^\]]*\])$')


def _expand(spec: str) -> list[int]:
    """``[0-3,7%4]`` into ``[0, 1, 2, 3, 7]`` -- how ``sacct`` lists tasks that have not started."""
    indices = []
    for part in spec.strip('[]').split('%')[0].split(','):
        low, _, high = part.partition('-')
        indices.extend(range(int(low), int(high or low) + 1))
    return indices


def shard_states(run: Runner, job_ids: Sequence[str]) -> dict[int, str]:
    """Each shard's latest Slurm state across one or more submissions (a resubmit supersedes)."""
    states: dict[int, str] = {}
    for job_id in job_ids:
        out = run(f'sacct -n -P -X -j {shlex.quote(job_id)} -o JobID,State', None)
        for line in out.splitlines():
            task, _, state = line.partition('|')
            match = _TASK.match(task.strip())
            if not match:
                continue
            index = match.group(2)
            for shard in _expand(index) if index.startswith('[') else [int(index)]:
                states[shard] = state.split()[0] if state else 'UNKNOWN'
    return states


def gather(run: Runner, run_dir: str) -> dict[int, list[dict[str, Any]]]:
    """Every shard result file written so far, keyed by shard index."""
    quoted = _sh_path(run_dir)
    out = run(f'for f in {quoted}/results/*.json; do [ -e "$f" ] && cat "$f" && echo; done; true', None)
    results = {}
    for line in filter(None, (raw.strip() for raw in out.splitlines())):
        record = json.loads(line)
        results[int(record['shard'])] = record['results']
    return results
