"""A commit's ARTIFACT: ONE file per sha that a job at that commit runs from, never a checkout.

A job needs a runnable thing, not the repository: its env, the commit's ``source_roots`` and the paths it
declares it reads (``[tool.lab_commons.hpc] artifact_paths``, e.g. the cases a campaign evaluates). A test
run legitimately needs the whole tree and keeps ``~/ci/trees/<sha>`` (:mod:`lab_commons.hpc.tree`); a job
gets ``~/ci/artifacts/<sha>.<suffix>`` and nothing else on the shared home. Measured 2026-10-17 on Ada: a
tree plus an unpacked env per sha cost ~46 k files against a 2 M file quota.

THE FORMAT IS A DECLARED CHOICE (:data:`FORMATS`, ``[job] artifact``), because which one a cluster can run
is a fact of its compute nodes:

* ``tarball`` -- one gzip tar, unpacked into node-local ``$TMPDIR`` at task start (node scratch is outside the
  home's file quota). Needs only ``tar`` on the node.
* ``image`` -- an Apptainer/Singularity image. REFUSED BY NAME, for good (user ruling 2026-10-19, HPC phase 6
  closed): ``uv sync --frozen`` from the committed lock already makes the env reproducible, and Ada offers
  only singularity 3.8.5 as a module, so an image buys nothing a tarball does not and costs a second build.

THE BUILD LEAVES ONE FILE. It runs on the login node in a scratch directory under ``$TMPDIR`` that is
removed when it ends: the commit is exported there (``git archive``, never a worktree), its env built there
with uv's cache there too (no resident build cache), and the members packed. Only a native build stays
keyed in ``~/ci/native/<key>`` (with its ``cargo-target``), as for a test run: a Rust rebuild costs minutes,
and retention removes it once unreferenced. An artifact that exists is reused as is: a commit's artifact is
a function of the commit and its declaration.

Inside the artifact: ``env/`` (the venv), ``tree/<path>`` for every source root and declared path,
``native/`` when declared, and ``activate.sh``, which exports the env relative to ``$ART`` -- the one
place the artifact says how it is entered. A job's task unpacks it, sources that file and runs in
``$ART/tree`` (:func:`enter_lines`), so a relative ``cases/...`` resolves as in a checkout.
"""

from __future__ import annotations

import shlex
from typing import Final

from lab_commons.hpc.builds import cargo_target
from lab_commons.hpc.cluster import Runner
from lab_commons.hpc.shell import BUILD_FAILED, ROOT, RunSpec, cache_dir, facts, keyed_build
from lab_commons.hpc.tree import NotSubmitted, fetch

__all__ = [
    'FORMATS',
    'IMAGE',
    'TARBALL',
    'ArtifactRefused',
    'artifact_path',
    'artifact_script',
    'build_artifact',
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
        '(docs-src/dev/hpc.md, artifacts); use artifact = "tarball"'
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


def artifact_path(sha: str, fmt: str = TARBALL) -> str:
    """The one file of *sha*'s artifact, under :data:`~lab_commons.hpc.shell.ROOT` (shell-quoted home)."""
    require(fmt)
    return f'{ROOT}/artifacts/{sha}.{_SUFFIX[fmt]}'


def artifact_script(spec: RunSpec, fmt: str = TARBALL) -> str:
    """Build *spec*'s artifact into its one file unless it exists; print ``@@@ facts`` with ``artifact=``."""
    target, builds, cache = artifact_path(spec.sha, fmt), spec.builds, cache_dir(spec.repo_url)
    if not builds.install:
        msg = 'an artifact needs the declared install (lab_commons.hpc.builds.builds_at)'
        raise ValueError(msg)
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
        f'A={target}',
        'if [ ! -s "$A" ]; then',
        f'  mkdir -p {ROOT}/artifacts',
        '  S=$(mktemp -d "${TMPDIR:-/tmp}/lab-ci-artifact.XXXXXX"); trap \'rm -rf "$S"\' EXIT',
        f'  mkdir -p "$S/tree" "$S/pack/tree" && git -C {cache} archive {spec.sha} | tar -x -C "$S/tree"',
        '  cd "$S/tree"',
        '  export E="$S/pack/env" UV_CACHE_DIR="$S/uv-cache" UV_LINK_MODE=copy',
        f'  if ! bash -eo pipefail -c {shlex.quote(install)} > "$S/build.log" 2>&1; then',
        f'    echo "{BUILD_FAILED} env"; tail -n {_LOG_TAIL} "$S/build.log"; exit 0',
        '  fi',
        f'  [ -x "$E/bin/python" ] || {{ echo "{BUILD_FAILED} no bin/python in the env"; exit 0; }}',
    ]
    if builds.native_key:
        native = f'{ROOT}/native/{builds.native_key}'
        cargo = f'export CARGO_TARGET_DIR="$HOME"/{cargo_target(builds.native_key)}\nmkdir -p "$CARGO_TARGET_DIR"'
        cargo += '\nexec 8> "$CARGO_TARGET_DIR.lock"; flock 8'
        build = (
            f'export VIRTUAL_ENV="$E" PATH="$E/bin:$PATH"\nexport LAB_CI_NATIVE="$N"\n{cargo}\n{builds.native_build}'
        )
        lines += [f'  export N={native}', *(f'  {line}' for line in keyed_build('"$N"', f'{native}.log', build))]
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
    return '\n'.join(lines)


def build_artifact(spec: RunSpec, run: Runner, where: str, fmt: str = TARBALL) -> str:
    """Make *spec*'s commit's artifact exist on *where*'s home; the path of its one file.

    Raises:
        NotSubmitted: a missing need, an unfetchable commit, or a failed build -- nothing was submitted.

    """
    fetch(spec, run, where)
    built = run(artifact_script(spec, fmt), None)
    if BUILD_FAILED in built:
        tail = built.split(BUILD_FAILED, 1)[1].strip()
        msg = f'the artifact for {spec.sha[:12]} failed to build on {where}; {tail}'
        raise NotSubmitted(msg)
    return facts(built)['artifact']


def enter_lines(sha: str, fmt: str = TARBALL) -> tuple[str, ...]:
    """The task-script lines that unpack *sha*'s artifact into node-local scratch and enter it."""
    return (
        'ART="${TMPDIR:-/tmp}/lab-artifact-${SLURM_JOB_ID}"',
        'trap \'rm -rf "$ART"\' EXIT',
        f'mkdir -p "$ART" && tar -xzf {artifact_path(sha, fmt)} -C "$ART"',
        '. "$ART/activate.sh"',
        'cd "$ART/tree"',
    )
