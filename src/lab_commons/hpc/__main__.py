"""``python -m lab_commons.hpc <verb>`` -- plan, submit, status, gather, retry, run {submit|gather|build}.

WHERE comes from this machine's grants (:mod:`lab_commons.hpc.grants`, the ``[hpc]`` table of
:mod:`lab_commons.config`); WHAT from the job file ``-c`` (:mod:`lab_commons.hpc.config`). The last
submission of a job file is recorded next to it as ``<job>.submission.json`` -- one placement per grant
the batch spans (:mod:`lab_commons.hpc.jobs`: its ssh targets, items, run directory, job ids, shards), so
``status``/``gather``/``retry`` need nothing more -- through whichever of those login hosts answers.
``gather -c <job> -o <out> --wait`` polls as ``status`` does every ``[policy] poll_seconds``, prints each
change once (``pending: <why>`` while no login host answers), and gathers once no shard is queued or running.

``run submit --scope full|incremental`` tests one commit on the cluster (:mod:`lab_commons.hpc.run`) and
returns once the first round is queued; ``incremental`` names its re-run ids with ``--only``, ``full``
takes none. ``run build --sha <sha> --repo-url <url> [--artifact tarball]`` builds that commit's ONE
artifact file (:mod:`lab_commons.hpc.artifact`) and prints the ``[job]`` lines that run ANY entry over a job
file's items from it (``submit -c``); it collects, submits and records nothing. ``run gather --sha <sha> -o <dir>`` is
one short call -- where the run is and exit :data:`PENDING`, or the record written to
``<dir>/run-<sha>-<part>-<req>.json.gz`` (exit 1 if any id is red, lost or missing); ``--wait``
blocks until the record. All three RUN ON THE CLUSTER LOGIN NODE with its own shell
(:func:`lab_commons.hpc.cluster.submitter`; ``forge serve`` drives them there) and are refused
on a box without ``sbatch`` -- no workstation submits over ssh. WHERE A RUN IS, AND WHAT THE CLUSTER CAN
TAKE, IS READ FROM THE FORGE: ``python -m lab_commons.dev.forge queue`` shows each run's progress on its
claim and each serving host's card (supply, partitions, quota), so no verb reaches the cluster to ask.
``-c`` is optional for ``run`` and only its ``[cost]``/``[policy]`` are read. ``run submit`` plans from what
the repository's last record measured
(:mod:`lab_commons.compute.learned`); a file never measured is priced at ``[cost]``. A retired verb is
refused by name (:mod:`lab_commons.hpc.retired`). ``deploy [--dry-run|--remove]`` makes this account's
login host a ``forge serve`` host in one run (:mod:`lab_commons.hpc.deploy`); ``tick <repo>`` is one
cron tick of it: the host refreshes the kit and the repo, then the repo's ``forge serve`` runs.

``allocate --until <iso> name=cpus/mem_gb ...`` writes the ONE expiring allocation record on the forge
(:mod:`lab_commons.hpc.allocation`), ``allocate --show`` prints it, ``allocate --clear`` deletes it. Every
planning verb resolves ``$LC_HPC_SESSION``'s share from that record, else derives it from Slurm
(:func:`lab_commons.hpc.allocate.resolve`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Final

from lab_commons.compute import learned
from lab_commons.durable import durable_text
from lab_commons.hpc import deploy, tick
from lab_commons.hpc.allocate import allocate, resolve
from lab_commons.hpc.artifact import FORMATS, TARBALL, ArtifactRefused, build_artifact
from lab_commons.hpc.builds import builds_at
from lab_commons.hpc.cluster import BUILD_TIMEOUT, NotOnCluster, Unreachable, runner_for, submitter
from lab_commons.hpc.config import Config, load_config
from lab_commons.hpc.grants import Grant, Machine, load_grants
from lab_commons.hpc.jobs import (
    Ensure,
    Reach,
    built_config,
    gather_job,
    plan_job,
    retry_job,
    settled,
    shard_counts,
    status_job,
    status_lines,
    submit_job,
)
from lab_commons.hpc.needsets import BASE, vocabulary_at
from lab_commons.hpc.retired import refusal
from lab_commons.hpc.run import submit_run
from lab_commons.hpc.shell import FULL, INCREMENTAL, RunSpec
from lab_commons.hpc.tree import NotSubmitted
from lab_commons.hpc.watch import settle
from lab_commons.log import emit

__all__ = ['main']

#: The producer contract's INCONCLUSIVE (``lab_commons.dev.producer.ProducerExit``, which hpc may not import):
#: ``run submit`` exits with it when nothing was submitted, so a failed build is never read as a red run.
_NOT_SUBMITTED = 3


def _state_path(config: Config) -> Path:
    if config.source is None:
        msg = 'a submission is recorded next to its job file; this config was not loaded from one'
        raise ValueError(msg)
    return config.source.with_suffix('.submission.json')


def _items(config: Config) -> list[Any]:
    items = json.loads(config.items_path().read_text(encoding='utf-8'))
    if not isinstance(items, list):
        msg = f'{config.items_path()} must hold a JSON list'
        raise TypeError(msg)
    return items


def _out(text: str) -> None:
    emit(text)


def _record(config: Config) -> dict[str, Any]:
    record = json.loads(_state_path(config).read_text(encoding='utf-8'))
    if 'placements' not in record:
        msg = (
            f'{_state_path(config)} records one grant and no placements -- written before a batch spanned '
            'every grant (lab_commons.hpc.jobs); submit again'
        )
        raise ValueError(msg)
    return record


def _save_record(config: Config, record: dict[str, Any]) -> None:
    durable_text(_state_path(config), json.dumps(record, indent=2))


def _ensure(config: Config, repo: Path) -> Ensure | None:
    """``[job] sha``: each chosen grant gets the commit's one artifact file, as ``run build`` makes it."""
    job = config.job
    if not job.sha:
        return None
    spec = RunSpec(sha=job.sha, repo_url=job.repo_url, pack_from=repo, builds=builds_at(repo, job.sha, python=''))

    def ensure(grant: Grant) -> None:
        build_artifact(spec, runner_for(grant, BUILD_TIMEOUT), grant.name, job.artifact)

    return ensure


def _gather(record: dict[str, Any], machine: Machine, reach: Reach, output: Path | None, budget: int) -> int:
    """Every placement's results merged in batch order; a silent grant named; a run over its file budget FAILS."""
    got = gather_job(record, machine, reach, budget=budget)
    shards = sum(p['shards'] for p in record['placements'])
    failed = sum(not r['ok'] for r in got.results)
    _out(f'{got.returned}/{shards} shards returned, {len(got.results)} items, {failed} item errors')
    for name in got.silent:
        _out(f'{name}: did not answer; its shards are not in this result (retry re-places them)')
    for line in got.earlier:
        _out(f'{line}: written by an earlier worker, one file per shard; read through the upgrade')
    for line in got.over:
        _out(f'OVER FILE BUDGET {line}')
    if output is not None:
        output.write_text(json.dumps(got.results), encoding='utf-8')
        _out(f'written {output}')
    return 1 if got.over else 0


def _gather_when_settled(
    config: Config, record: dict[str, Any], machine: Machine, reach: Reach, output: Path | None
) -> int:
    """``gather --wait``: poll as ``status`` does, print each change once, gather once no shard is active."""
    last = None
    while True:
        try:
            found = shard_counts(record, machine, reach)
        except Unreachable as silent:
            found = [('cluster', silent)]
        silent = [f'{g}: {c}' for g, c in found if isinstance(c, Unreachable)]
        summary = f'pending: {"; ".join(silent)}' if silent else '; '.join(status_lines(found))
        if summary != last:
            _out(summary)
            last = summary
        if settled(found):
            return _gather(record, machine, reach, output, config.policy.file_budget)
        _sleep(config.policy.poll_seconds)


def _jobs(
    verb: str, config: Config, machine: Machine, reach: Reach, *, repo: Path, output: Path | None, wait: bool = False
) -> int:
    """The job-file verbs: plan, submit, status, gather, retry -- every grant the batch spans."""
    config = built_config(config)
    if verb == 'plan':
        for placement in plan_job(config, len(_items(config)), machine, reach):
            start, stop = placement.items
            _out(f'{placement.grant.name}: items {start}-{stop}: {placement.plan.describe()}')
        return 0
    if verb == 'submit':
        record = submit_job(
            config, _items(config), machine, reach, stamp=time.strftime('%Y%m%d-%H%M%S'), ensure=_ensure(config, repo)
        )
        _save_record(config, record)
        for p in record['placements']:
            start, stop = p['items']
            _out(f'submitted job {p["job_ids"][0]} to {p["grant"]} (items {start}-{stop}); {p["run_dir"]}')
        return 0
    record = _record(config)
    if verb == 'status':
        for line in status_job(record, machine, reach):
            _out(line)
        return 0
    if verb == 'gather':
        if wait:
            return _gather_when_settled(config, record, machine, reach, output)
        return _gather(record, machine, reach, output, config.policy.file_budget)
    for line in retry_job(config, _items(config), record, machine, reach, ensure=_ensure(config, repo)):
        _out(line)
    _save_record(config, record)
    return 0


def _only(path: Path | None) -> frozenset[str] | None:
    """The ids an ``--only`` file selects, one per line; ``None`` runs everything collected."""
    if path is None:
        return None
    return frozenset(line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip())


def _run_submit(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    try:
        builds = builds_at(args.repo, args.sha, python=args.python)
    except ValueError as undeclared:
        emit(f'run submit refused: {undeclared}', err=True)
        return 2
    spec = RunSpec(
        sha=args.sha,
        repo_url=args.repo_url,
        collect=args.collect,
        select=args.select,
        vocabulary=vocabulary_at(args.repo, args.sha),
        part=args.part,
        python=args.python,
        pack_from=args.repo,
        builds=builds,
        only=_only(args.only),
    )
    try:
        state = submit_run(
            spec,
            machine,
            submitter(),
            cost=config.cost,
            policy=config.policy,
            history=learned.learned(spec.repo_url),
        )
    except NotSubmitted as why:
        emit(f'run submit inconclusive: {why}', err=True)
        return _NOT_SUBMITTED
    _out(
        f'submitted run {spec.sha} ({spec.scope}) as round 0 of {len(state["rounds"])}; '
        f'wait for it: python -m lab_commons.hpc run gather --sha {spec.sha} -o <dir> [--wait]'
    )
    return 0


def _run_build(args: argparse.Namespace, _machine: Machine, _config: Config) -> int:
    try:
        builds = builds_at(args.repo, args.sha, python=args.python)
    except ValueError as undeclared:
        emit(f'run build refused: {undeclared}', err=True)
        return 2
    spec = RunSpec(sha=args.sha, repo_url=args.repo_url, python=args.python, pack_from=args.repo, builds=builds)
    try:
        built = build_artifact(spec, submitter()(None), 'this login node', args.artifact)
    except (NotSubmitted, ArtifactRefused) as why:
        emit(f'run build inconclusive: {why}', err=True)
        return _NOT_SUBMITTED
    _out(f'built {built}; a job file runs any entry from it with:')
    _out('[job]')
    _out(f'sha = "{args.sha}"')
    _out(f'repo_url = "{args.repo_url}"')
    _out(f'artifact = "{args.artifact}"')
    return 0


#: ``time.sleep``, named so a test drives the poll loop without waiting.
_sleep = time.sleep


def _run_gather(args: argparse.Namespace, machine: Machine, config: Config) -> int:
    return settle(
        [args.sha], machine, submitter(), config, args.output, part=args.part, sleep=_sleep if args.wait else None
    )


#: Each ``run`` step and the flags it cannot do without.
_RUN_NEEDS: Final = {
    'submit': ('--sha', '--repo-url', '--scope'),
    'gather': ('--sha', '--output'),
    'build': ('--sha', '--repo-url'),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc', description=__doc__.splitlines()[0])
    parser.add_argument('verb', choices=['plan', 'submit', 'status', 'gather', 'retry', 'run'])
    parser.add_argument('step', nargs='?', choices=list(_RUN_NEEDS), help='run only: submit, then gather')
    parser.add_argument('-c', '--config', type=Path, help='the job file; run needs none ([cost]/[policy])')
    parser.add_argument(
        '-o', '--output', type=Path, help='gather: merged item results (JSON); run gather: the record directory'
    )
    parser.add_argument(
        '--wait',
        action='store_true',
        help='gather -c: poll status, print each change, gather once no shard is queued or running; '
        'run gather: block until the run has its record',
    )
    group = parser.add_argument_group('run')
    group.add_argument('--scope', choices=[FULL, INCREMENTAL], help='run submit: every id, or only --only')
    group.add_argument('--sha', help='the full commit id to test')
    group.add_argument('--repo-url', help='read-only HTTPS remote the cluster fetches from')
    group.add_argument('--collect', default='', help='pytest arguments selecting the tests (paths, -m ...)')
    group.add_argument('--select', default='', help='marker expression choosing the tests (one -m, joined)')
    group.add_argument('--python', default='', help='interpreter request for `uv venv --python`')
    group.add_argument(
        '--part', default=BASE, help=f'run submit/gather: the part (a need-set) the run runs; default {BASE}'
    )
    group.add_argument(
        '--repo',
        type=Path,
        default=Path.cwd(),
        help='local repository, READ only: its declaration at --sha; a pack if the remote lacks it',
    )
    group.add_argument('--only', type=Path, help='a file of node ids, one per line: run just these (incremental)')
    group.add_argument('--artifact', choices=sorted(FORMATS), default=TARBALL, help='run build: the artifact format')
    return parser


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """``run <step>``: check the step's flags, then dispatch it."""
    if args.step is None:
        parser.error('run needs a step: submit, then gather')
    if args.wait and args.step != 'gather':
        parser.error('--wait belongs to gather (-c) and run gather')
    values = {
        '--sha': args.sha,
        '--repo-url': args.repo_url,
        '--output': args.output,
        '--scope': args.scope,
    }
    missing = [flag for flag in _RUN_NEEDS[args.step] if not values[flag]]
    if missing:
        parser.error(f'run {args.step} needs {", ".join(missing)}')
    if args.step == 'submit' and (args.scope == INCREMENTAL) != (args.only is not None):
        parser.error('--scope incremental names its re-run ids with --only; --scope full takes no --only')
    config = load_config(args.config) if args.config else Config()
    steps = {'submit': _run_submit, 'gather': _run_gather, 'build': _run_build}
    try:
        machine = load_grants()
        return steps[args.step](args, resolve(machine) if args.step == 'submit' else machine, config)
    except NotOnCluster as off:
        emit(f'run {args.step} refused: {off}', err=True)
        return 2


def _job_config(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Config:
    """The job file ``-c`` names. Never a guessed file in the cwd."""
    if args.config is None:
        parser.error(
            f'{args.verb} reads a job file and the submission recorded beside it; pass -c <job file> '
            '(the one submit used). A commit test is `run submit`/`run gather --sha`, which need none.'
        )
    if not args.config.is_file():
        parser.error(f'no job file at {args.config}; pass -c <job file> naming an existing one')
    return load_config(args.config)


def _refuse_retired(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    """A retired verb, step or flag is refused by name with its remedy."""
    if argv and (refused := refusal('lab_commons.hpc', argv[0])):
        parser.error(refused)
    if argv[:1] == ['run'] and len(argv) > 1 and (refused := refusal('lab_commons.hpc run', argv[1])):
        parser.error(refused)
    for word in argv:
        if (refused := refusal('run submit', word.split('=', 1)[0])) is not None:
            parser.error(refused)


def main(argv: list[str] | None = None) -> int:
    """Parse the verb and dispatch it against this machine's grants."""
    parser = _parser()
    argv = sys.argv[1:] if argv is None else argv
    direct = {'deploy': deploy.main, 'allocate': allocate}
    if argv and argv[0] in direct:
        return direct[argv[0]](argv[1:])
    if argv[:1] == ['tick'] and argv[1:]:
        return max(tick.tick(name, home=Path.home()) for name in argv[1:])
    _refuse_retired(parser, argv)
    args = parser.parse_args(argv)
    if args.verb == 'run':
        return _run(parser, args)
    machine = load_grants()
    if args.verb in {'plan', 'submit', 'retry'}:
        machine = resolve(machine)
    if args.step is not None:
        parser.error(f'{args.verb} takes no step')
    if args.wait and args.verb != 'gather':
        parser.error('--wait belongs to gather (-c) and run gather')
    config = _job_config(parser, args)
    return _jobs(args.verb, config, machine, runner_for, repo=args.repo, output=args.output, wait=args.wait)


if __name__ == '__main__':
    sys.exit(main())
