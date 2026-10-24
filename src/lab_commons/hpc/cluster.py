"""Talk to the cluster: probe it, submit a plan as one job array, read back states and results.

EVERYTHING GOES THROUGH A RUNNER. A :data:`Runner` takes one shell command (and optional stdin) and
returns its stdout; :func:`local_runner` runs it here (already on a login node) and is the only runner
that SUBMITS -- a part's producer runs on the cluster (``forge serve``); :func:`ssh_runner` runs it on a
login node from a workstation, for the job-file verbs (``plan``/``submit``/``status``/``gather``/``retry``) only;
where a commit's run is, and what the cluster can take, is read from the forge. Files reach the
cluster as ``cat > path`` on stdin, so a run needs no ``scp`` and no shared filesystem with the caller
-- and a test drives every function below with a fake runner and no cluster.

THE RUN DIRECTORY IS THE RECORD, AND IT IS THREE FILES (:func:`run_home`): the manifest (items and shard
ranges), the array script and the ONE results file every task appends to (:mod:`lab_commons.hpc.worker`). A
task's output goes to node-local ``$TMPDIR`` and only its tail reaches the results file. Re-running a failed
shard re-reads the same manifest; nothing about a run lives only in the caller's memory. A run written by an
earlier worker (a results file per shard) still gathers: :func:`gather` upgrades it on read.
"""

from __future__ import annotations

import functools
import json
import shlex
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import lab_commons.launch as _door
from lab_commons.hpc.config import Config, JobSpec
from lab_commons.hpc.grants import Grant
from lab_commons.hpc.plan import Plan
from lab_commons.hpc.slurm import Snapshot, parse_snapshot, probe_command
from lab_commons.hpc.worker import ITEM, RESULTS, SHARD
from lab_commons.log import log
from lab_commons.outcomes import Status
from lab_commons.outcomes.record import OUTCOME, Event

__all__ = [
    'BUILD_TIMEOUT',
    'SUBMIT_LIMIT',
    'TERMINAL_OK',
    'NotOnCluster',
    'Runner',
    'Submission',
    'SubmitLimit',
    'Unreachable',
    'failover_runner',
    'files_of',
    'gather',
    'local_runner',
    'probe',
    'render_script',
    'run_home',
    'runner_for',
    'ssh_runner',
    'submit',
    'submit_bands',
    'submitter',
    'throttle',
]

#: Slurm's refusal of a submit past the per-user QOS ``MaxSubmitPU`` -- a CAPACITY signal, never a failed run.
SUBMIT_LIMIT: Final = 'QOSMaxSubmitJobPerUserLimit'


class SubmitLimit(RuntimeError):
    """``sbatch`` refused a band on :data:`SUBMIT_LIMIT`; the bands this round already queued were cancelled."""


#: Run one shell command, feeding *stdin*, and return its stdout; raise on a non-zero exit.
type Runner = Callable[[str, str | None], str]

#: The only array-task state that means the shard's results file should exist.
TERMINAL_OK: Final = 'COMPLETED'

_TIMEOUT: Final = 300.0

#: A login-node build of a tree's venv (a Rust extension, say) needs far more than a probe's five minutes.
BUILD_TIMEOUT: Final = 3 * 3600.0

#: Seconds ``ssh`` waits for a login host to answer before the next one is tried.
CONNECT_TIMEOUT: Final = 10

#: ``ssh`` exits 255 when it could not reach the host -- the command never ran, so another host may run it.
_SSH_UNREACHABLE: Final = 255


class Unreachable(RuntimeError):
    """``ssh`` could not reach a login host; the command did not run."""


def _completed(argv: Sequence[str], stdin: str | None, timeout: float = _TIMEOUT) -> str:
    """Run *argv*; stdin goes as UTF-8 BYTES.

    BYTES, BECAUSE TEXT MODE REWRITES IT. On Windows a text-mode pipe turns every LF into CRLF, and the
    array script that reached the cluster that way was refused outright -- measured on a real cluster 2026-10-08:
    ``sbatch: error: Batch script contains DOS line breaks``. What the caller wrote is what the cluster
    gets.
    """
    done = _door.run(
        argv,
        input=None if stdin is None else stdin.encode('utf-8'),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out, err = done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace')
    if done.returncode == _SSH_UNREACHABLE and argv[0] == 'ssh':
        msg = f'ssh could not reach {argv[-2]}: {err.strip() or out.strip()}'
        raise Unreachable(msg)
    if done.returncode != 0:
        msg = f'{argv[0]} exited {done.returncode}: {err.strip() or out.strip()}'
        raise RuntimeError(msg)
    return out


def local_runner(command: str, stdin: str | None = None, *, timeout: float = _TIMEOUT) -> str:
    """Run *command* with ``bash -lc`` on this machine (a login node); *timeout* per command."""
    return _completed(['bash', '-lc', command], stdin, timeout)


class NotOnCluster(RuntimeError):
    """A submitting step was asked for on a box with no ``sbatch``; the message names where it runs."""


def submitter(timeout: float = BUILD_TIMEOUT) -> Callable[[Grant], Runner]:
    """The runner every SUBMITTING step uses: this login node's own shell, whatever the grant.

    A part's producer runs ON the cluster, pulled by ``forge serve`` (design 2026-10-11): no
    workstation submits over ``ssh``, because that path is the VPN dependency the design removes.
    Off a cluster (no ``sbatch`` on PATH) it raises :class:`NotOnCluster`.
    """
    if shutil.which('sbatch') is None:
        msg = (
            'this box has no sbatch: submitting and gathering run ON the cluster login node, where '
            '`python -m dev_lab.dev.forge serve` produces the part (dev-lab docs-src/dev/hpc.md, deployment); '
            'from any machine `python -m dev_lab.dev.forge queue` shows where each run is'
        )
        raise NotOnCluster(msg)
    run = functools.partial(local_runner, timeout=timeout)
    return lambda _grant: run


def ssh_runner(host: str, timeout: float = _TIMEOUT) -> Runner:
    """A runner that executes on *host* over key-based, non-interactive ``ssh``.

    *timeout* is per command; a login-node build (a per-tree install) needs far more than a probe.
    """

    def run(command: str, stdin: str | None = None) -> str:
        argv = [
            'ssh',
            '-o',
            'BatchMode=yes',
            '-o',
            f'ConnectTimeout={CONNECT_TIMEOUT}',
            host,
            f'bash -lc {shlex.quote(command)}',
        ]
        return _completed(argv, stdin, timeout)

    return run


def failover_runner(runners: Sequence[tuple[str, Runner]]) -> Runner:
    """A runner over several login hosts of ONE cluster: the first that answers is used from then on.

    A host that raises :class:`Unreachable` is skipped and the skip is logged; the chosen host stays chosen,
    so one run never straddles two hosts for no reason. Every host refusing raises naming all of them.
    """
    if not runners:
        msg = 'a failover runner needs at least one host'
        raise ValueError(msg)
    order = list(runners)

    def run(command: str, stdin: str | None = None) -> str:
        refusals = []
        while order:
            target, runner = order[0]
            try:
                return runner(command, stdin)
            except Unreachable as refused:
                refusals.append(str(refused))
                order.pop(0)
                if order:
                    log(f'hpc: {target} did not answer; failing over to {order[0][0]}', level='WARNING')
        msg = 'no login host answered: ' + '; '.join(refusals)
        raise Unreachable(msg)

    return run


def runner_for(grant: Grant, timeout: float = _TIMEOUT) -> Runner:
    """``ssh`` to the grant's hosts in order, the first that answers; credentials from ``~/.ssh/config``."""
    return failover_runner([(target, ssh_runner(target, timeout)) for target in grant.targets])


def probe(run: Runner, account: str = '') -> Snapshot:
    """Read the cluster once -- see :mod:`lab_commons.hpc.slurm`; a named account also reads its usage."""
    return parse_snapshot(run(probe_command(account), None), account)


def _sh_path(path: str) -> str:
    """Quote a path for the shell while keeping a leading ``~`` expandable."""
    if path == '~' or path.startswith('~/'):
        return '"$HOME"' + (shlex.quote(path[1:]) if len(path) > 1 else '')
    return shlex.quote(path)


def render_script(plan: Plan, config: Config, run_dir: str) -> str:
    """The array task script. The array range itself is an ``sbatch`` argument, so a resubmit reuses this."""
    directives = [
        f'--job-name={config.job.name}',
        f'--partition={",".join(plan.partitions)}',
        f'--account={plan.account}' if plan.account else '',
        f'--qos={plan.qos}' if plan.qos else '',
        '--nodes=1',
        '--ntasks=1',
        f'--cpus-per-task={plan.cpus}',
        f'--mem={plan.mem_mb}M',
        f'--gres=gpu:{plan.gpus}' if plan.gpus else '',
        f'--time={plan.minutes}',
        f'--comment={plan.comment}' if plan.comment else '',
        # A task's output is node-local (below); Slurm writes no file of its own on the shared home.
        '--output=/dev/null',
    ]
    worker = f'{config.job.python} -m lab_commons.hpc.worker'
    lines = ['#!/bin/bash', *(f'#SBATCH {d}' for d in directives if d), 'set -eo pipefail']
    lines += [*plan.setup, f'cd {_sh_path(config.job.workdir)}', *config.job.setup]
    lines += [
        (
            'export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK" OPENBLAS_NUM_THREADS="$SLURM_CPUS_PER_TASK" '
            'MKL_NUM_THREADS="$SLURM_CPUS_PER_TASK"'
        ),
        f'M={_sh_path(run_dir)}/manifest.json',
        'LOG="${TMPDIR:-/tmp}/lab-hpc-${SLURM_ARRAY_JOB_ID}-${SLURM_ARRAY_TASK_ID}.log"',
        'code=0',
        f'{worker} "$M" "$SLURM_ARRAY_TASK_ID" > "$LOG" 2>&1 || code=$?',
        # The worker is the env's own lab_commons: one without --close leaves no close line, so its shard is
        # simply not returned. The task's exit stays the worker's either way.
        f'{worker} --close "$M" "$SLURM_ARRAY_TASK_ID" "$code" "$LOG" || true',
        'rm -f "$LOG"',
        'exit "$code"',
    ]
    return '\n'.join(lines) + '\n'


def run_home(job: JobSpec) -> str:
    """Where a job's run directories live: ``~/ci/jobs/<sha>`` for a job at a commit, else ``<workdir>/.lab-hpc``.

    A commit's jobs carry the sha in their submit directory, which ``squeue %Z`` reports, so retention reads
    which artifact a queued or running job needs (:mod:`dev_lab.dev.retentionplan`).
    """
    return f'~/ci/jobs/{job.sha}' if job.sha else f'{job.workdir.rstrip("/")}/.lab-hpc'


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
    run_dir = f'{run_home(config.job)}/{config.job.name}-{stamp}'
    quoted = _sh_path(run_dir)
    if only is None:
        manifest = {'entry': config.job.entry, 'items': items, 'shards': [list(s) for s in plan.shards]}
        run(f'mkdir -p {quoted} && cat > {quoted}/manifest.json', json.dumps(manifest))
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


def submit_bands(
    run: Runner, plans: Sequence[Plan], config: Config, items: list[Any], *, stamp: str
) -> tuple[str, list[str]]:
    """ONE run directory, manifest and script for every band of a round, and ONE ``sbatch`` per band.

    A Slurm array asks ONE memory for every task, so items of different memory classes are different
    arrays: each *plan* covers its own consecutive shard indices of the one manifest (their shards
    concatenated, in order), and its ``sbatch`` overrides the script's ``--mem``, ``--time``,
    ``--partition`` and ``--qos`` with its own. Returns the run directory and each band's job id, in order.
    A band refused on :data:`SUBMIT_LIMIT` cancels the bands already queued and raises :class:`SubmitLimit`.
    """
    run_dir = f'{run_home(config.job)}/{config.job.name}-{stamp}'
    quoted = _sh_path(run_dir)
    shards = [list(s) for plan in plans for s in plan.shards]
    manifest = {'entry': config.job.entry, 'items': items, 'shards': shards}
    run(f'mkdir -p {quoted} && cat > {quoted}/manifest.json', json.dumps(manifest))
    run(f'cat > {quoted}/job.sh', render_script(plans[0], config, run_dir))
    job_ids, first = [], 0
    for plan in plans:
        indices = range(first, first + len(plan.shards))
        first += len(plan.shards)
        own = [
            f'--mem={plan.mem_mb}M',
            f'--time={plan.minutes}',
            f'--partition={",".join(plan.partitions)}',
            *([f'--qos={plan.qos}'] if plan.qos else []),
        ]
        array = _array_spec(indices, plan.throttle)
        try:
            out = run(f'cd {quoted} && sbatch --parsable {" ".join(own)} --array={array} job.sh', None)
        except RuntimeError as refused:
            if SUBMIT_LIMIT not in str(refused):
                raise
            if job_ids:  # a round is whole or not at all: the bands it queued would hold submit slots
                run(f'scancel {" ".join(job_ids)}', None)
            raise SubmitLimit(str(refused)) from refused
        job_id = out.strip().split(';')[0]
        if not job_id.isdigit():
            msg = f'sbatch did not return a job id: {out!r}'
            raise RuntimeError(msg)
        job_ids.append(job_id)
    return run_dir, job_ids


#: ``scancel``'s line for an id Slurm no longer holds: a job already gone is what a cancel asks for.
_GONE: Final = 'Invalid job id specified'


def scancel(shell: Runner, ids: Sequence[str], *flags: str) -> None:
    """``scancel`` *ids*; an id Slurm no longer holds is not a refusal (Ada 2026-10-23), any other line is.

    ``scancel`` still signals every id it holds when one of them is gone, and exits 1 for the gone one.
    """
    try:
        shell(shlex.join(('scancel', *flags, *ids)), None)
    except RuntimeError as refused:
        errors = [line for line in str(refused).splitlines() if 'error' in line]
        if not errors or any(_GONE not in line for line in errors):
            raise


def throttle(run: Runner, job_id: str, tasks: int) -> None:
    """Set array *job_id*'s concurrency limit to *tasks* (``scontrol update ... ArrayTaskThrottle``).

    An array that finished between the state read and the update has nothing left to throttle: Slurm's
    ``Job has already finished`` is a no-op (Ada 2026-10-20: it dropped a live run). Any other refusal raises.
    """
    try:
        run(f'scontrol update JobId={shlex.quote(job_id)} ArrayTaskThrottle={max(1, tasks)}', None)
    except RuntimeError as exc:
        if _ALREADY_FINISHED not in str(exc):
            raise


#: Slurm's answer to an update of a job that has ended (``slurm_strerror(ESLURM_ALREADY_DONE)``).
_ALREADY_FINISHED: Final = 'Job has already finished'


#: Separates the results file from an earlier worker's per-shard files in :func:`gather`'s one read.
_EARLIER: Final = '@@@ earlier-layout'


def gather(run: Runner, run_dir: str) -> dict[int, list[dict[str, Any]]]:
    """Every RETURNED shard's item results, keyed by shard index -- read from the run's one results file.

    A run an earlier worker wrote holds one ``results/<shard>.json`` per shard instead; it is read the same
    way (FORMATS-UPGRADE-ON-READ: jobs still running across the change must gather), never written.
    """
    quoted = _sh_path(run_dir)
    out = run(
        f'cat {quoted}/{RESULTS} 2>/dev/null; printf "\\n{_EARLIER}\\n"; '
        f'for f in {quoted}/results/*.json; do [ -e "$f" ] && cat "$f" && echo; done; true',
        None,
    )
    current, _, earlier = out.partition(f'\n{_EARLIER}\n')
    return {**_upgraded(earlier), **_returned(current)}


def _returned(text: str) -> dict[int, list[dict[str, Any]]]:
    """The results file's returned shards: each closed with exit 0, with the latest line of each of its items."""
    lines = [line for line in text.splitlines() if line.strip()]
    events = []
    for number, line in enumerate(lines, start=1):
        try:
            events.append(Event.from_plain(json.loads(line)))
        except ValueError:
            if number < len(lines):
                raise  # only the last line may be a killed writer's torn tail
    items: dict[int, dict[str, Any]] = {}
    codes: dict[int, int] = {}
    for event in events:
        if event.kind == OUTCOME and event.cell is not None:
            ok = event.cell.status is Status.OK
            why = '' if event.cell.reason is None else event.cell.reason.text
            items[int(event.key)] = {**event.data, 'ok': ok, 'status': event.cell.status.value, 'error': why}
        elif event.kind == ITEM:  # a pre-Cell worker's line (FORMATS-UPGRADE-ON-READ)
            items[int(event.key)] = {**event.data, 'status': 'ok' if event.data['ok'] else 'error'}
        elif event.kind == SHARD:
            codes[int(event.key)] = int(event.data['code'])
    returned: dict[int, list[dict[str, Any]]] = {shard: [] for shard, code in codes.items() if code == 0}
    for index in sorted(items):
        data = items[index]
        if data['shard'] in returned:
            result = {'index': index, 'ok': data['ok'], 'status': data['status']}
            result |= {'value': data['value']} if data['ok'] else {'error': data['error']}
            returned[data['shard']].append(result)
    return returned


def _upgraded(text: str) -> dict[int, list[dict[str, Any]]]:
    """An earlier worker's per-shard files, one ``{shard, results}`` object per line, read one way."""
    results = {}
    for line in filter(None, (raw.strip() for raw in text.splitlines())):
        record = json.loads(line)
        results[int(record['shard'])] = [{**r, 'status': 'ok' if r['ok'] else 'error'} for r in record['results']]
    return results


def files_of(run: Runner, run_dir: str) -> tuple[int, bool]:
    """How many files *run_dir* holds, and whether an earlier worker wrote it (a ``results/`` directory)."""
    quoted = _sh_path(run_dir)
    out = run(f'find {quoted} -type f | wc -l; [ -d {quoted}/results ] && echo earlier; true', None).split()
    return (int(out[0]) if out else 0), 'earlier' in out[1:]
