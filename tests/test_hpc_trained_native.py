"""A TRAINED native (e.g. profile-guided) runs its build as a Slurm job and is selected per job (2026-11-27).

User ruling 2026-11-27: a research job's artifact must be able to carry the consumer's production (PGO) kernel, not
only the plain release build. Design: ``.claude/memory/2026/11/27/a-trained-native-build-runs-as-a-slurm-job.md``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from lab_commons.hpc.artifact import artifact_path, artifact_script, enter_lines
from lab_commons.hpc.builds import PLAIN, TRAINED, builds_at, read_builds
from lab_commons.hpc.config import load_config
from lab_commons.hpc.declared import InvalidDeclaration
from lab_commons.hpc.grants import Grant
from lab_commons.hpc.jobs import built_config
from lab_commons.hpc.shell import RunSpec

_GIT: Final = shutil.which('git') or 'git'

PLAIN_ONLY: Final = """[project]
name = "x"
[project.optional-dependencies]
dev = ["pytest"]
rust = ["maturin"]
[tool.dev_lab.dep]
extras = ["dev"]
needs = { rust = "rust" }
[tool.lab_commons.hpc]
dependency_inputs = ["pyproject.toml", "uv.lock"]
source_roots = ["src"]
[tool.lab_commons.hpc.native]
inputs = ["rust"]
build = "make plain"
"""
DECLARED: Final = (
    PLAIN_ONLY
    + """[tool.lab_commons.hpc.native.trained]
prefetch = "cargo fetch"
inputs = ["src"]
build = "make pgo"
needs = ["rust"]
cpus = 32
mem = "64 GiB"
wall = "50 min"
"""
)


GRANT: Final = Grant(
    user='u',
    hosts=('login',),
    slurm_account='acct',
    partitions=('shortq', 'defq', 'ampereq'),
    qos={'ampereq': 'free'},
    setup=('module load git',),
    exclude_nodes=('bad1',),
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', check=True
    ).stdout.strip()


@pytest.fixture
def sha_of(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    for name, text in {
        'pyproject.toml': DECLARED,
        'uv.lock': 'rev = "1"',
        'rust/lib.rs': 'fn x() {}',
        'src/x.py': '',
    }.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding='utf-8')
    _git(repo, 'add', '-A', '.')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'c')
    return repo, _git(repo, 'rev-parse', 'HEAD')


def test_the_trained_build_is_declared_beside_the_plain_one_which_stays_selectable() -> None:
    builds = read_builds(DECLARED)
    assert builds.native_build == 'make plain'
    assert builds.trained is not None
    assert (builds.trained.prefetch, builds.trained.build, builds.trained.needs) == (
        'cargo fetch',
        'make pgo',
        ('rust',),
    )
    assert (builds.trained.cpus, builds.trained.mem_gb, builds.trained.wall_s) == (32, 64.0, 3000)
    assert builds.trained.inputs == ('src',)
    assert read_builds(PLAIN_ONLY).trained is None


def test_a_trained_table_names_its_build_and_its_cost() -> None:
    for missing in ('build = "make pgo"\n', 'cpus = 32\n', 'mem = "64 GiB"\n', 'wall = "50 min"\n'):
        with pytest.raises(InvalidDeclaration, match='trained'):
            read_builds(DECLARED.replace(missing, ''))
    with pytest.raises(InvalidDeclaration, match='trained'):
        read_builds(DECLARED.replace('needs = ["rust"]\n', 'unknown = 1\n'))


def test_the_trained_native_has_its_own_key_and_its_env_carries_the_build_needs(sha_of: tuple[Path, str]) -> None:
    repo, sha = sha_of
    plain = builds_at(repo, sha, python='3.13')
    trained = builds_at(repo, sha, python='3.13', native=TRAINED)
    assert (plain.native, trained.native) == (PLAIN, TRAINED)
    assert trained.native_key != plain.native_key, 'a profile is never served as the plain build, nor the reverse'
    assert '--extra rust' in trained.install
    assert '--extra rust' not in plain.install, 'only the trained build needs its build tools'
    # what the training reads keys the trained native only: a profile is retrained on the tree that drives it
    (repo / 'src' / 'x.py').write_text('changed = 1\n', encoding='utf-8')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-am', 'src')
    moved = _git(repo, 'rev-parse', 'HEAD')
    assert builds_at(repo, moved, python='3.13', native=TRAINED).native_key != trained.native_key
    assert builds_at(repo, moved, python='3.13').native_key == plain.native_key


def test_a_trained_native_on_a_commit_that_declares_none_is_refused_by_name(tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    (repo / 'pyproject.toml').write_text(PLAIN_ONLY, encoding='utf-8')
    _git(repo, 'add', '-A', '.')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'c')
    with pytest.raises(InvalidDeclaration, match=r'native\.trained'):
        builds_at(repo, _git(repo, 'rev-parse', 'HEAD'), python='3.13', native=TRAINED)


def test_the_trained_artifact_is_its_own_file() -> None:
    assert artifact_path('a' * 40).endswith('/' + 'a' * 40 + '.tar.gz')
    assert artifact_path('a' * 40, native=TRAINED).endswith('/' + 'a' * 40 + '.trained.tar.gz')
    assert any('.trained.tar.gz' in line for line in enter_lines('a' * 40, native=TRAINED))


def test_the_trained_build_runs_in_a_slurm_job_of_the_grant_after_a_login_node_prefetch(
    sha_of: tuple[Path, str],
) -> None:
    repo, sha = sha_of
    spec = RunSpec(sha=sha, repo_url='https://h/o/r.git', builds=builds_at(repo, sha, python='3.13', native=TRAINED))
    script = artifact_script(spec, grant=GRANT)
    assert 'sbatch --wait' in script
    for directive in ('--account=acct', '--partition=shortq,defq', '--cpus-per-task=32', '--mem=65536M',
                      '--time=50', '--exclude=bad1'):  # fmt: skip
        assert directive in script, directive
    assert 'ampereq' not in script, 'a partition that needs a qos is not a build partition'
    assert 'cargo fetch' in script
    assert script.index('cargo fetch') < script.index('sbatch --wait'), 'the network step runs on the login node'
    assert 'make pgo' in script
    assert 'make plain' not in script, 'no fallback to the plain build'
    assert 'PROVENANCE' in script
    assert '.trained.tar.gz' in script
    assert 'mktemp -d "${TMPDIR' not in script, 'the compute node must see the tree and env it builds from'
    # planted control: the plain build runs inline on the login node, with no job
    plain = artifact_script(RunSpec(sha=sha, repo_url='https://h/o/r.git', builds=builds_at(repo, sha, python='3.13')))
    assert 'sbatch' not in plain
    assert 'make plain' in plain


def test_a_trained_build_needs_the_grant_it_runs_on(sha_of: tuple[Path, str]) -> None:
    repo, sha = sha_of
    spec = RunSpec(sha=sha, repo_url='https://h/o/r.git', builds=builds_at(repo, sha, python='3.13', native=TRAINED))
    with pytest.raises(ValueError, match='grant'):
        artifact_script(spec)


def test_a_job_selects_the_trained_native_and_enters_its_artifact(tmp_path: Path) -> None:
    job = tmp_path / 'job.toml'
    job.write_text(
        '[job]\nentry = "m:f"\nitems = "i.json"\nsha = "' + 'b' * 40 + '"\nrepo_url = "https://h/o/r.git"\n'
        'native = "trained"\n',
        encoding='utf-8',
    )
    config = load_config(job)
    assert config.job.native == TRAINED
    assert any('.trained.tar.gz' in line for line in built_config(config).job.setup)
    job.write_text(job.read_text(encoding='utf-8').replace('"trained"', '"fast"'), encoding='utf-8')
    with pytest.raises(ValueError, match='native'):
        load_config(job)
