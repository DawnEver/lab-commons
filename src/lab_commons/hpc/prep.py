"""THE PREP JOB: a run's fetch, env build, native build, needs probe and collection run as ONE Slurm job.

User ruling 2026-11-27 (collaboration architecture v2): nothing of a run is built or collected on the LOGIN node.
:func:`lab_commons.hpc.run.submit_run` only ships text there (the item runner, the collector, the pack of an
unpushed commit, this script) and ``sbatch``-es :func:`prep_script`; the compute node fetches the commit
(Ada compute nodes have outbound HTTPS, measured 2026-11-27, job 8516573), probes the declared needs, builds the
env and the native build (each keyed by its inputs, built once under a ``flock`` and stamped
``.lab-ci-installed``: :func:`lab_commons.hpc.shell.keyed_build`), and collects -- once for the run, once per
declared resource marker. Every step's output is a file in :func:`prep_dir`; :func:`read_prep` reads them back
in one call.

THE ARRAY IS DEFERRED TO THE NEXT ADVANCE, never planned blind: a round's items are the COLLECTED ids, packed by
their files' measured walls and peaks, less the ids another part owns (``handed``) and the ids carried from an
earlier run of the same env -- none of which exists before collection. A conservative estimate submitted with
``--dependency=afterok`` would need an items manifest the login node cannot write without collecting. So the
first :func:`lab_commons.hpc.gather.gather_run` after the prep job left Slurm plans and submits round 0.

The dev-lab tick reads the account's build state through :func:`env_ready` and :func:`prep_needed`.

AN ENVIRONMENT PREP JOB (:func:`submit_env_prep`) is the same door for a served repo's own environment: the cron
tick, ON the login node, ``sbatch``-es a shell line as one job of the account's grant keyed by the environment key,
and never queues a second one for a key in flight.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Final

from lab_commons.hpc.builds import Builds
from lab_commons.hpc.cluster import Runner, local_runner, probe
from lab_commons.hpc.config import Cost, Policy
from lab_commons.hpc.grants import Machine, load_grants
from lab_commons.hpc.layout import BIN, ENVS, NATIVE, PACKS, REQUIRE_ROOT, RUNS
from lab_commons.hpc.plan import Plan, allocate
from lab_commons.hpc.shell import (
    COLLECT_MODULE,
    ENTRY_MODULE,
    ITEM_MODULE,
    RunSpec,
    build_script,
    collect_command,
    fetch_script,
    needs_script,
    pack,
)

__all__ = [
    'INSTALLED',
    'PREP_SECONDS',
    'STEPS',
    'env_prep_dir',
    'env_prep_name',
    'env_ready',
    'prep_dir',
    'prep_needed',
    'prep_script',
    'read_prep',
    'submit_env_prep',
    'submit_prep',
]

#: The wall a prep job asks: a cold env plus a cold native build, measured well under it.
PREP_SECONDS: Final = 3600

#: The stamp a finished keyed build leaves in its directory (:func:`lab_commons.hpc.shell.keyed_build`).
INSTALLED: Final = '.lab-ci-installed'

#: The prep's steps, in order: each one's output is ``<prep_dir>/<step>.txt``; ``also-<i>`` follow ``collected``.
STEPS: Final = ('needs', 'fetch', 'built', 'collected')

#: The heredoc delimiter that carries each step's script verbatim (quoted: nothing in it is expanded twice).
_STEP: Final = 'LAB_CI_PREP_STEP'

#: Marks each file's start in :func:`read_prep`'s one read.
_FILE: Final = '@@@ prep-file'


def prep_dir(sha: str, part: str) -> str:
    """Where the prep of *sha*'s run of *part* keeps its script, log and step outputs."""
    return f'{RUNS}/{sha}/{part}/prep'


def _step(name: str, script: str) -> list[str]:
    """Write *script* verbatim beside its output, then run it with no stdin (nothing in it can eat the next line)."""
    return [
        f'cat > "$P/{name}.sh" <<\'{_STEP}\'',
        script,
        _STEP,
        f'bash "$P/{name}.sh" > "$P/{name}.txt" 2>&1 < /dev/null',
    ]


def prep_script(spec: RunSpec, plan: Plan, *, name: str) -> str:
    """The ``sbatch`` script of *spec*'s prep, on *plan*'s partition, account and QOS, under the run's job *name*.

    A step that fails stops the job (exit 1) with its output kept; :func:`read_prep` and the gather name it.
    """
    where = prep_dir(spec.sha, spec.part)
    directives = [
        f'--job-name={name}',
        f'--partition={",".join(plan.partitions)}',
        f'--account={plan.account}' if plan.account else '',
        f'--qos={plan.qos}' if plan.qos else '',
        f'--exclude={",".join(plan.exclude_nodes)}' if plan.exclude_nodes else '',
        '--nodes=1',
        '--ntasks=1',
        f'--cpus-per-task={plan.cpus}',
        f'--mem={plan.mem_mb}M',
        f'--time={plan.minutes}',
        '--output=prep.log',
    ]
    lines = ['#!/bin/bash', *(f'#SBATCH {d}' for d in directives if d), 'set -u', *plan.setup, REQUIRE_ROOT]
    lines += [f'P={where}', 'mkdir -p "$P"', 'rm -f "$P"/*.txt']
    lines += [*_step('needs', needs_script(spec)), 'grep -q "^@@@ missing " "$P/needs.txt" && exit 1']
    lines += [*_step('fetch', fetch_script(spec)), '[ "$(tail -n 1 "$P/fetch.txt")" = have ] || exit 1']
    lines += [*_step('built', build_script(spec)), 'grep -q "^@@@ build-failed" "$P/built.txt" && exit 1']
    lines += _step('collected', collect_command(spec))
    for index, marker in enumerate(() if spec.entry else spec.vocabulary.resources):
        lines += _step(f'also-{index}', collect_command(spec, also=marker))
    lines += ['echo ok > "$P/done.txt"']
    return '\n'.join(lines) + '\n'


def submit_prep(spec: RunSpec, run: Runner, plan: Plan, *, name: str) -> str:
    """Ship the prep's inputs to the login node as TEXT, ``sbatch`` :func:`prep_script`; the prep's job id.

    The login node runs only ``cat`` (the item runner, the collector, a pack, the script) and ``sbatch``:
    no ``uv``, no ``pytest``, no ``cargo``, no ``git``.
    """
    here = Path(__file__).parent
    where = prep_dir(spec.sha, spec.part)
    run(
        f'{REQUIRE_ROOT} && mkdir -p {BIN} {PACKS} {where} && cat > {BIN}/{ITEM_MODULE}.py',
        _source(here, 'pytest_item'),
    )
    run(f'cat > {BIN}/{COLLECT_MODULE}.py', _source(here, 'collect'))
    run(f'cat > {BIN}/{ENTRY_MODULE}.py', _source(here, 'entry_item'))
    if spec.pack_from is not None:  # indexed by the prep's fetch only when origin lacks the commit
        run(f'base64 -d > {PACKS}/{spec.sha}.pack', pack(spec.pack_from, spec.sha))
    run(f'cat > {where}/prep.sbatch', prep_script(spec, plan, name=name))
    out = run(f'cd {where} && sbatch --parsable prep.sbatch', None)
    job_id = out.strip().split(';')[0]
    if not job_id.isdigit():
        msg = f'sbatch did not return a job id for the prep: {out!r}'
        raise RuntimeError(msg)
    return job_id


def _source(here: Path, module: str) -> str:
    return (here / f'{module}.py').read_text(encoding='utf-8')


def read_prep(run: Runner, sha: str, part: str) -> dict[str, str]:
    """Every step output of the prep, ``{step: text}``, in ONE read; a step that never ran is absent."""
    where = prep_dir(sha, part)
    said = run(
        f'for f in {where}/*.txt; do [ -f "$f" ] || continue; '
        f'echo "{_FILE} $(basename "$f" .txt)"; cat "$f"; done; true',
        None,
    )
    files: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in said.splitlines():
        if line.startswith(f'{_FILE} '):
            current = files.setdefault(line[len(_FILE) + 1 :].strip(), [])
        elif current is not None:
            current.append(line)
    return {step: '\n'.join(lines) + '\n' for step, lines in files.items()}


def _installed(run: Runner, directory: str) -> bool:
    return run(f'[ -f {directory}/{INSTALLED} ] && echo yes || true', None).strip() == 'yes'


def env_ready(run: Runner, env_key: str) -> bool:
    """Whether the env of *env_key* (the hash over its inputs, the lock among them) is built and stamped."""
    return _installed(run, f'{ENVS}/{shlex.quote(env_key)}')


def prep_needed(run: Runner, builds: Builds) -> bool:
    """Whether a prep for *builds* must BUILD: its env or its native build is not stamped on this account.

    A run's prep job is always submitted (its commit must be fetched and collected); this says whether that
    job is the cheap one, so a tick can order a build-bound run behind the ones whose env is ready.
    """
    if not env_ready(run, builds.env_key):
        return True
    return bool(builds.native_key) and not _installed(run, f'{NATIVE}/{shlex.quote(builds.native_key)}')


#: Slurm states of a job still in flight (``squeue`` answers for it; ``sacct`` may lag behind).
_LIVE: Final = frozenset({'PENDING', 'RUNNING', 'REQUEUED', 'RESIZING', 'SUSPENDED', 'CONFIGURING', 'COMPLETING'})

#: The lines of a failed job's log its one-line report quotes.
_TAIL: Final = 5


def env_prep_name(key: str) -> str:
    """The Slurm job name of the environment prep of *key*: ``envprep-<key12>``."""
    return f'envprep-{key[:12]}'


def env_prep_dir(root: str, key: str) -> str:
    """Where the environment prep of *key* keeps its script, its job id and its log, under the account *root*."""
    return f'{root}/jobs/envprep/{key[:12]}'


def env_prep_script(checkout: Path, root: str, plan: Plan, *, name: str, script: str) -> str:
    """The ``sbatch`` script running the shell line *script* in *checkout* on *plan*; it exits as *script* does."""
    where = env_prep_dir(root, name.removeprefix('envprep-'))
    directives = [
        f'--job-name={name}',
        f'--partition={",".join(plan.partitions)}',
        f'--account={plan.account}' if plan.account else '',
        f'--qos={plan.qos}' if plan.qos else '',
        f'--exclude={",".join(plan.exclude_nodes)}' if plan.exclude_nodes else '',
        '--nodes=1',
        '--ntasks=1',
        f'--cpus-per-task={plan.cpus}',
        f'--mem={plan.mem_mb}M',
        f'--time={plan.minutes}',
        f'--output={where}/prep.log',
        f'--chdir={checkout.as_posix()}',
    ]
    lines = ['#!/bin/bash', *(f'#SBATCH {d}' for d in directives if d), 'set -u', *plan.setup]
    lines += [f'export LAB_ROOT={shlex.quote(root)}', script]
    return '\n'.join(lines) + '\n'


def submit_env_prep(
    checkout: Path,
    key: str,
    *,
    python: str,
    root: str,
    script: str,
    kit: str,
    retry: bool = False,
    machine: Machine | None = None,
    run: Runner = local_runner,
) -> str:
    """Submit *script* (a POSIX shell line) as ONE Slurm job keyed by *key*; one line saying what it did.

    Runs ON the account's login node (the cron tick) through *run*, its own shell: only ``squeue``, ``sacct``,
    ``cat``, ``tail``, ``mkdir`` and ``sbatch`` reach it. The job is the prep job's shape -- one task of the
    grant :func:`~lab_commons.hpc.plan.allocate` picks among *machine*'s (default: this login's ``[hpc]``),
    :data:`PREP_SECONDS` of wall -- named :func:`env_prep_name`, its log in :func:`env_prep_dir` under *root*.

    - a job of that name queued or running: ``job <id> already queued for <key>``, nothing submitted;
    - the last job of *key* ended non-zero (``sacct``) and its log is there, under the same *kit*:
      ``job <id> failed: <log>: <tail>``, nothing submitted -- a failure is reported, never retried blind;
    - such a failure recorded under ANOTHER kit (*kit* names what builds the env -- the caller's kit commit, written
      beside the job id at submit; none recorded counts as another) is stale, the kit that failed is not the one
      asking: ``job <id> failed under kit <old>, not <kit>: submitted job <new>``, once (the new job records *kit*);
    - *retry* (the caller's explicit door) resubmits over a failure: ``job <id> failed; retry asked: submitted ...``;
    - else ``submitted job <id>``. Removing the log, or a new key, also re-arms it.

    *python* is the account's interpreter as the deploy declares it; *script* already names the one it runs.
    A job's failure never raises; a broken invocation (an empty key or script, no grant, ``sbatch`` answering
    no id) does.
    """
    del python  # the contract carries it; the job runs *script* as given
    if not key.strip() or not script.strip():
        msg = f'submit_env_prep needs a key and a script, got key={key!r} script={script!r}'
        raise ValueError(msg)
    name, where = env_prep_name(key), env_prep_dir(root, key)
    queued = run(f'squeue -h --me -n {shlex.quote(name)} -o %i', None).split()
    if queued:
        return f'job {queued[0]} already queued for {key}'
    if not kit.strip():
        msg = f'submit_env_prep needs the kit that builds {key}, got kit={kit!r}'
        raise ValueError(msg)
    why = ''
    last = run(f'cat {shlex.quote(where)}/job 2>/dev/null || true', None).strip()
    if last.isdigit() and (failed := _failed(run, last, where)) is not None:
        under = run(f'cat {shlex.quote(where)}/kit 2>/dev/null || true', None).strip()
        if retry:
            why = f'job {last} failed; retry asked: '
        elif under != kit:
            why = f'job {last} failed under kit {under or "(unrecorded)"}, not {kit}: '
        else:
            return f'job {last} failed: {where}/prep.log: {failed}'
    grants = (machine or load_grants()).grants
    snapshots = [(g, probe(run, g.slurm_account, exclude=g.exclude_nodes)) for g in grants]
    _, plan = allocate(
        1,
        Cost(seconds=PREP_SECONDS),
        snapshots,
        session=None,
        policy=Policy(shard_minutes_max=PREP_SECONDS / 60),
        seconds=[PREP_SECONDS],
        rerun_safe=True,
    )
    run(
        f'mkdir -p {shlex.quote(where)} && cat > {shlex.quote(where)}/prep.sbatch',
        env_prep_script(checkout, root, plan, name=name, script=script),
    )
    out = run(f'cd {shlex.quote(where)} && sbatch --parsable prep.sbatch', None)
    job_id = out.strip().split(';')[0]
    if not job_id.isdigit():
        msg = f'sbatch did not return a job id for the environment prep of {key}: {out!r}'
        raise RuntimeError(msg)
    run(f'cat > {shlex.quote(where)}/job', f'{job_id}\n')
    run(f'cat > {shlex.quote(where)}/kit', f'{kit}\n')
    return f'{why}submitted job {job_id}'


def _failed(run: Runner, job_id: str, where: str) -> str | None:
    """The log tail of *job_id* when it ended non-zero and its log is there, else ``None`` (resubmittable)."""
    states = run(f'sacct -n -X -P -j {job_id} -o State', None).split()
    if not states or states[0] == 'COMPLETED' or states[0] in _LIVE:
        return None
    log = f'{shlex.quote(where)}/prep.log'
    tail = run(f'[ -f {log} ] && tail -n {_TAIL} {log} && echo @@@ log || true', None)
    if not tail.rstrip().endswith('@@@ log'):
        return None
    lines = [line.strip() for line in tail.rstrip().removesuffix('@@@ log').splitlines() if line.strip()]
    return f'{states[0]}: ' + (' / '.join(lines) or '(empty log)')
