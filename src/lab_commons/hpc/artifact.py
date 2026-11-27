"""A commit's ARTIFACT: ONE file per sha that a job at that commit runs from, never a checkout.

A job needs a runnable thing, not the repository: its env, the commit's ``source_roots`` and the paths it
declares it reads (``[tool.lab_commons.hpc] artifact_paths``, e.g. the cases a campaign evaluates). A test
run legitimately needs the whole tree and keeps ``<root>/runs/<sha>/tree`` (:mod:`lab_commons.hpc.tree`); a job
gets ``<root>/artifacts/<sha>.<suffix>`` and nothing else. Measured 2026-10-17 on Ada: a
tree plus an unpacked env per sha cost ~46 k files against a 2 M file quota.

THE FORMAT IS A DECLARED CHOICE (:data:`FORMATS`, ``[job] artifact``), because which one a cluster can run
is a fact of its compute nodes:

* ``tarball`` -- one gzip tar, unpacked into node-local ``$TMPDIR`` at task start (node scratch is outside the
  home's file quota). Needs only ``tar`` on the node.
* ``image`` -- an Apptainer/Singularity image. REFUSED BY NAME, for good (user ruling 2026-10-19, HPC phase 6
  closed): ``uv sync --frozen`` from the committed lock already makes the env reproducible, and Ada offers
  only singularity 3.8.5 as a module, so an image buys nothing a tarball does not and costs a second build.

THE BUILD LEAVES ONE FILE. It runs on the login node in a scratch directory under ``$TMPDIR`` that is
removed when it ends: the commit is exported there (``git archive``, never a worktree) WITH every submodule at
the commit it pins (a path dependency may point into one), its env built there
with uv's cache there too (no resident build cache), and the members packed. Only a native build stays
keyed in ``<root>/native/<key>`` (with its ``cargo-target``), as for a test run: a Rust rebuild costs minutes,
and retention removes it once unreferenced. An artifact that exists is reused as is: a commit's artifact is
a function of the commit and its declaration.

Inside the artifact: ``env/`` (the venv), ``tree/<path>`` for every source root and declared path,
``native/`` when declared, and ``activate.sh``, which exports the env relative to ``$ART`` -- the one
place the artifact says how it is entered. A job's task unpacks it, sources that file and runs in
``$ART/tree`` (:func:`enter_lines`), so a relative ``cases/...`` resolves as in a checkout.

A TRAINED NATIVE (``[job] native = "trained"``, :class:`lab_commons.hpc.builds.Trained`) is the one build that does not
run on the login node: it runs the workload it is trained on. The artifact is then ``<sha>.trained.<suffix>``, its
scratch is made under the account root (compute nodes see it, login-node ``$TMPDIR`` they do not), the declared
``prefetch`` runs on the login node (it may need the network), and the keyed build runs in ONE Slurm job of the
grant (``sbatch --wait``: its account, its partitions that need no qos, its exclusions and setup lines, the declared
cpus, mem and wall). The native must leave ``PROVENANCE``; the artifact facts print it. There is no fallback to the
plain build. Design: ``.claude/memory/2026/11/27/a-trained-native-build-runs-as-a-slurm-job.md``.
"""

from __future__ import annotations

import math
import shlex
from typing import Final

from lab_commons.hpc.builds import PLAIN, TRAINED, Trained, cargo_target
from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.grants import Grant
from lab_commons.hpc.layout import ARTIFACTS, NATIVE, REQUIRE_ROOT, ROOT, UV_CACHE
from lab_commons.hpc.shell import BUILD_FAILED, RunSpec, cache_dir, export_submodules_script, facts, keyed_build
from lab_commons.hpc.tree import NotSubmitted, fetch
from lab_commons.log import emit

__all__ = [
    'FORMATS',
    'IMAGE',
    'TARBALL',
    'ArtifactRefused',
    'artifact_path',
    'artifact_script',
    'build_artifact',
    'build_partitions',
    'enter_lines',
    'require',
]

TARBALL: Final = 'tarball'
IMAGE: Final = 'image'

#: Each artifact format and why it is refused; an empty reason means it is built.
FORMATS: Final[dict[str, str]] = {
    TARBALL: '',
    IMAGE: (
        'an image artifact is not built (HPC phase 6 closed, 2026-10-19): uv sync --frozen from the committed '
        'lock already makes the env reproducible, and Ada offers only singularity 3.8.5 as a module '
        '(dev-lab docs-src/dev/hpc.md, artifacts); use artifact = "tarball"'
    ),
}

#: Each format's file suffix.
_SUFFIX: Final = {TARBALL: 'tar.gz'}

#: Lines of the build log a refusal quotes.
_LOG_TAIL: Final = 40


class ArtifactRefused(ValueError):
    """The declared artifact format is unknown, or cannot be built yet; the message names why."""


def require(fmt: str) -> None:
    """Refuse *fmt* when it is not a format, or one :data:`FORMATS` refuses."""
    if fmt not in FORMATS:
        msg = f'artifact = {fmt!r} is not a format; the formats are {", ".join(sorted(FORMATS))}'
        raise ArtifactRefused(msg)
    if FORMATS[fmt]:
        raise ArtifactRefused(FORMATS[fmt])


def artifact_path(sha: str, fmt: str = TARBALL, native: str = PLAIN) -> str:
    """The one file of *sha*'s artifact carrying its *native* build, under the account layout's artifacts.

    The plain and the trained artifact of one commit are two files, so one never stands in for the other.
    """
    require(fmt)
    kind = '' if native == PLAIN else f'.{native}'
    return f'{ARTIFACTS}/{sha}{kind}.{_SUFFIX[fmt]}'


def build_partitions(grant: Grant) -> tuple[tuple[str, ...], str]:
    """Where a trained build's job may start: the grant's partitions that need no qos; else its first, with its qos.

    A partition mapped to a qos (``ampereq = "free"``) is a special-purpose one; a build asks the general ones.
    """
    general = tuple(p for p in grant.partitions if p not in grant.qos)
    if general or not grant.partitions:
        return general, ''
    first = grant.partitions[0]
    return (first,), grant.qos[first]


#: The head of every native build step's ``bash -c``: the env active and ``$LAB_CI_NATIVE`` set (``$E``/``$N``).
_ENV: Final = 'export VIRTUAL_ENV="$E" PATH="$E/bin:$PATH"\nexport LAB_CI_NATIVE="$N"\n'


def _trained_lines(trained: Trained, grant: Grant, native: str, build: str) -> list[str]:
    """Login-node lines: prefetch, then build the trained native *native* in one waited-for Slurm job of *grant*."""
    partitions, qos = build_partitions(grant)
    job_script = '\n'.join(
        ['#!/bin/bash', 'set -eo pipefail', *grant.setup, 'cd "$S/tree"', *keyed_build('"$N"', f'{native}.log', build)]
    )
    flags = (
        '--job-name=lab-native-trained',
        f'--account={grant.slurm_account}',
        f'--partition={",".join(partitions)}' if partitions else '',
        f'--qos={qos}' if qos else '',
        '--nodes=1',
        '--ntasks=1',
        f'--exclude={",".join(grant.exclude_nodes)}' if grant.exclude_nodes else '',
        f'--cpus-per-task={trained.cpus}',
        f'--mem={math.ceil(trained.mem_gb * 1024)}M',
        f'--time={math.ceil(trained.wall_s / 60)}',
        '--output="$N.job.log"',
    )
    sbatch = f'sbatch --wait --parsable {" ".join(f for f in flags if f)} "$N.job.sh"'
    logs = f'tail -n {_LOG_TAIL} "$N.log" 2>/dev/null; tail -n 20 "$N.job.log" 2>/dev/null; exit 0'
    no_build = f'echo "{BUILD_FAILED} the trained native job left no build, log=$N.log"'
    no_provenance = (
        f'echo "{BUILD_FAILED} the trained native $N carries no PROVENANCE: its build writes one beside the native, '
        'naming what it was trained on"'
    )
    lines = [f'  export N={native} S', '  if [ ! -f "$N/.lab-ci-installed" ]; then']
    if trained.prefetch:
        lines += [
            f'    if ! bash -eo pipefail -c {shlex.quote(_ENV + trained.prefetch)} > "$N.prefetch.log" 2>&1; then',
            (
                f'      echo "{BUILD_FAILED} the trained native prefetch, log=$N.prefetch.log"; '
                f'tail -n {_LOG_TAIL} "$N.prefetch.log"; exit 0'
            ),
            '    fi',
        ]
    lines += [
        f'    printf \'%s\\n\' {shlex.quote(job_script)} > "$N.job.sh"',
        f'    if ! {sbatch} > "$N.sbatch.out" 2>&1; then',
        f'      echo "{BUILD_FAILED} the trained native job, sbatch: $(tail -n 3 "$N.sbatch.out"), log=$N.log"; {logs}',
        '    fi',
        '  fi',
        f'  [ -f "$N/.lab-ci-installed" ] || {{ {no_build}; {logs}; }}',
        f'  [ -s "$N/PROVENANCE" ] || {{ {no_provenance}; exit 0; }}',
    ]
    return lines


def artifact_script(spec: RunSpec, fmt: str = TARBALL, *, grant: Grant | None = None) -> str:
    """Build *spec*'s artifact into its one file unless it exists; print ``@@@ facts`` with ``artifact=``.

    A trained native (``spec.builds.native``) builds in a Slurm job of *grant*, which it therefore needs.
    """
    builds, cache = spec.builds, cache_dir(spec.repo_url)
    target = artifact_path(spec.sha, fmt, builds.native)
    if not builds.install:
        msg = 'an artifact needs the declared install (lab_commons.hpc.builds.builds_at)'
        raise ValueError(msg)
    trained = builds.trained if builds.native == TRAINED else None
    if builds.native == TRAINED and (trained is None or grant is None):
        msg = 'a trained native builds in a Slurm job of the grant it runs on: pass the grant, declare native.trained'
        raise ValueError(msg)
    scratch = (
        '"${TMPDIR:-/tmp}/lab-ci-artifact.XXXXXX"' if trained is None else f'{NATIVE}/.artifact-{spec.sha[:12]}.XXXXXX'
    )
    python = f' --python {shlex.quote(spec.python)}' if spec.python else ''
    members = ' '.join(
        shlex.quote(p.rstrip('/')) for p in dict.fromkeys((*builds.source_roots, *builds.artifact_paths))
    )
    path = ':'.join(
        ['$ART/tree/' + root.rstrip('/') for root in builds.source_roots]
        + (['$ART/native'] if builds.native_key else [])
    )
    install = '\n'.join([
        f'uv venv -q{python} "$E"',
        'export VIRTUAL_ENV="$E" PATH="$E/bin:$PATH" UV_PROJECT_ENVIRONMENT="$E"',
        builds.install,
    ])  # fmt: skip
    lines = [
        'set -eo pipefail',
        REQUIRE_ROOT,
        f'A={target}',
        'if [ ! -s "$A" ]; then',
        f'  mkdir -p {ARTIFACTS} {NATIVE}',
        f'  S=$(mktemp -d {scratch}); trap \'rm -rf "$S"\' EXIT',
        f'  mkdir -p "$S/tree" "$S/pack/tree" && git -C {cache} archive {spec.sha} | tar -x -C "$S/tree"',
        '  cd "$S/tree"',
        *export_submodules_script(spec.repo_url, cache, spec.sha),
        f'  export E="$S/pack/env" UV_CACHE_DIR={UV_CACHE} UV_LINK_MODE=copy',
        f'  if ! bash -eo pipefail -c {shlex.quote(install)} > "$S/build.log" 2>&1; then',
        f'    echo "{BUILD_FAILED} env"; tail -n {_LOG_TAIL} "$S/build.log"; exit 0',
        '  fi',
        f'  [ -x "$E/bin/python" ] || {{ echo "{BUILD_FAILED} no bin/python in the env"; exit 0; }}',
    ]
    if builds.native_key:
        native = f'{NATIVE}/{builds.native_key}'
        cargo = f'export CARGO_TARGET_DIR={ROOT}/{cargo_target(builds.native_key)}\nmkdir -p "$CARGO_TARGET_DIR"'
        cargo += '\nexec 8> "$CARGO_TARGET_DIR.lock"; flock 8'
        if trained is None or grant is None:  # the plain build (a trained one without its grant was refused above)
            build = f'{_ENV}{cargo}\n{builds.native_build}'
            lines += [f'  export N={native}', *(f'  {line}' for line in keyed_build('"$N"', f'{native}.log', build))]
        else:
            lines += _trained_lines(trained, grant, native, f'{_ENV}{cargo}\n{trained.build}')
        lines += ['  cp -a "$N" "$S/pack/native"']
    lines += [
        f'  for p in {members}; do mkdir -p "$S/pack/tree/$(dirname "$p")"; cp -a "$S/tree/$p" "$S/pack/tree/$p"; done',
        (
            '  printf \'%s\\n\' \'export VIRTUAL_ENV="$ART/env" PATH="$ART/env/bin:$PATH" '
            f'PYTHONPATH="{path}${{PYTHONPATH:+:$PYTHONPATH}}"\' > "$S/pack/activate.sh"'
        ),
        '  tar -czf "$A.part" -C "$S/pack" . && mv "$A.part" "$A"',
        'fi',
        'echo "@@@ facts"',
        'echo "artifact=$A"',
    ]
    if trained is not None:
        lines += ['echo "provenance=$(tar -xzOf "$A" ./native/PROVENANCE 2>/dev/null | tr \'\\n\' \' \')"']
    return '\n'.join(lines)


def build_artifact(spec: RunSpec, run: Runner, where: str, fmt: str = TARBALL, *, grant: Grant | None = None) -> str:
    """Make *spec*'s commit's artifact exist on *where*'s home; the path of its one file.

    A trained native needs *grant*, whose Slurm job builds it; its ``PROVENANCE`` is printed.

    Raises:
        NotSubmitted: a missing need, an unfetchable commit, or a failed build -- nothing was submitted.

    """
    fetch(spec, run, where)
    built = run(artifact_script(spec, fmt, grant=grant), None)
    if BUILD_FAILED in built:
        tail = built.split(BUILD_FAILED, 1)[1].strip()
        msg = f'the artifact for {spec.sha[:12]} failed to build on {where}; {tail}'
        raise NotSubmitted(msg)
    found = facts(built)
    if 'provenance' in found:
        emit(f'{where}: {found["artifact"]} carries a trained native, PROVENANCE: {found["provenance"].strip()}')
    return found['artifact']


def enter_lines(sha: str, fmt: str = TARBALL, native: str = PLAIN) -> tuple[str, ...]:
    """The task-script lines that unpack *sha*'s artifact (its *native* build) into node-local scratch and enter it."""
    return (
        'ART="${TMPDIR:-/tmp}/lab-artifact-${SLURM_JOB_ID}"',
        'trap \'rm -rf "$ART"\' EXIT',
        f'mkdir -p "$ART" && tar -xzf {artifact_path(sha, fmt, native)} -C "$ART"',
        '. "$ART/activate.sh"',
        'cd "$ART/tree"',
    )
