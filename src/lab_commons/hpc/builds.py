r"""CLUSTER BUILD REUSE -- a venv and a native build keyed by their REAL inputs, never by the commit.

THE REPO DECLARES ITS INPUTS ONCE, in its pyproject (read at the commit being judged)::

    [tool.lab_commons.hpc]
    needs = ["cargo", "cc"]                                 # login-node commands beyond git and uv
    dependency_inputs = ["pyproject.toml", "uv.lock"]      # what the venv is a function of
    source_roots = ["src"]                                  # put on PYTHONPATH from each sha's tree
    artifact_paths = ["cases"]                              # what a job reads beside them (lab_commons.hpc.artifact)
    native = { inputs = ["rust"], build = "pip install --target $LAB_CI_NATIVE ./rust/pybind" }
    # optional, beside the plain build: a TRAINED native (e.g. profile-guided) a job selects with [job] native,
    # declared as the table [tool.lab_commons.hpc.native.trained]: prefetch, inputs, build, needs, cpus, mem, wall
    # (class Trained)
    timeout = { incremental = "5 min", full = "50 min" }    # pytest --timeout per run scope (absent: the repo's own)
    item_wall = { multiple = 10, floor = "5 min", unmeasured = "1 h", ceiling = "2 h" }  # per-test kill (defaults)
    priced = "tests/priced.toml"                            # measured peaks/walls, no margin (read_priced)

Every duration is a quantity string (user ruling 2026-11-25, :mod:`lab_commons.quantity`); the unit-suffixed
spellings ``timeout_s`` and ``item_wall``'s ``floor_s``/``unmeasured_s``/``ceiling_s`` are refused with their new line.

THE VENV'S BUILD COMMAND IS DERIVED, never declared: ``uv sync --frozen`` of the extras the repo's
``[tool.dev_lab.dep]`` selects for the run's NEED-SET (:func:`lab_commons.extras.sync_command`), so a part or job
that needs nothing heavy builds an environment without it, and each need-set keys its own venv. A declared
``install`` is retired and refused by name.

A venv lives in ``<root>/envs/<env key>`` and a native build in ``<root>/native/<native key>``; each sha is
only a ``git worktree`` whose ``source_roots`` reach the venv through ``PYTHONPATH`` -- no per-sha
editable install. A fresh sha with unchanged inputs therefore builds NOTHING. The native build runs with
``$LAB_CI_NATIVE`` naming its output directory, which is put on ``PYTHONPATH`` after the source roots.

A TRAINED native (:class:`Trained`) is the plain build's selectable alternative: its build runs the workload it is
trained on, so it runs as a Slurm job of the grant (:mod:`lab_commons.hpc.artifact`), its key is its own (a
profile is never served as the plain build, nor the reverse) and its env adds the ``needs`` its build declares.
Design: ``.claude/memory/2026/11/27/a-trained-native-build-runs-as-a-slurm-job.md``.

A key is a SHA-256 over the git object ids of the declared inputs AT THE COMMIT (``git rev-parse
<sha>:<path>`` -- a read-only call; an absent path keys as absent) plus the build command and the
interpreter request. The committed ``uv.lock`` is one of those inputs and it pins every dependency revision
(PIN + FRESHNESS, user ruling 2026-10-17): the lock's hash keys the venv, a new lock rebuilds it, and no
remote is asked -- the same commit builds the same venv on every host and every day.
EVERY ENV HAS A KEY, AND THE KEY IS EVERY INPUT THAT BUILDS IT: the install command, the needs and the
interpreter request always; the declared inputs when there are some, else the commit itself (a venv
per commit). Measured 2026-10-10: a resubmit with a corrected install reused the tree's venv built by
the first, because the venv was keyed by the tree alone.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

import lab_commons.launch as _door
from lab_commons.extras import sync_command
from lab_commons.hpc.declared import InvalidDeclaration, kit_tables
from lab_commons.quantity import QuantityRefused, Renamed, form, parse, refuse_renamed

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    'FULL',
    'INCREMENTAL',
    'NATIVES',
    'PLAIN',
    'RETIRED_INSTALL',
    'SCOPES',
    'TRAINED',
    'Builds',
    'ItemWall',
    'Trained',
    'builds_at',
    'key_of',
    'read_builds',
    'read_priced',
]

_GIT: Final = shutil.which('git') or 'git'

#: Hex digits of a build key -- enough to never collide among one user's builds, short in a path.
_KEY_HEX: Final = 24

#: A run's scope: every collected id, or only the re-run ids that complete a base part (``RunSpec.only``).
FULL: Final = 'full'
INCREMENTAL: Final = 'incremental'
SCOPES: Final = (FULL, INCREMENTAL)

#: Which native build a job runs (``[job] native``): the declared release build, or the declared trained one.
PLAIN: Final = 'plain'
TRAINED: Final = 'trained'
NATIVES: Final = (PLAIN, TRAINED)


@dataclass(frozen=True)
class Trained:
    """``native.trained``: a native build that runs its workload, so it runs in a Slurm job of the grant.

    *prefetch* runs first on the login node, in the exported tree with the env active, and may use the network (e.g.
    ``cargo fetch``); *build* then runs on a compute node with *cpus*/*mem_gb*/*wall_s*, writes the native into
    ``$LAB_CI_NATIVE`` with a ``PROVENANCE`` file there, and must not need the network. *needs* is the need-set
    (``[tool.dev_lab.dep] needs``) its env carries beside the job's own. *inputs* are the paths its training reads
    beyond ``native.inputs`` (the workload's own source): they key the trained native, never the plain one.
    """

    build: str
    cpus: int
    mem_gb: float
    wall_s: int
    prefetch: str = ''
    needs: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemWall:
    """THE PER-TEST WALL the item runner enforces from OUTSIDE pytest (:mod:`lab_commons.hpc.pytest_item`).

    pytest-timeout runs inside the test's interpreter and cannot interrupt a C loop holding the GIL. Measured
    on Ada: a test spun at 100% CPU for 20 h, and a verdict waited 4 h on two hung items under a 21 h Slurm
    limit. A test with a measured duration is bounded at ``multiple`` times it, never under ``floor_s``; one
    never measured gets ``unmeasured_s``; NO test gets more than ``ceiling_s``. These defaults are the one place
    the numbers live.

    THE CEILING, and why a measurement cannot lift it: a "measurement" includes a kill's FLOOR, which every wall
    kill doubles (:meth:`lab_commons.compute.measured.Measured.killed`), so ``multiple`` times it grows without
    bound. Ada 2026-10-24..26: eight forge jobs each held a core 34-41 h on one hung winding test whose wall read
    776553..1051465 s -- nine to twelve days, past every Slurm limit, so the runner's kill never fired. Two hours
    is twenty times the slowest test a downstream suite measured passing (~11 min) and a fraction of any shard's
    wall; a test needing more is a test to split, not a node to hold.
    """

    multiple: float = 10.0
    floor_s: int = 300
    unmeasured_s: int = 3600
    ceiling_s: int = 7200

    def bound(self, measured_s: float | None) -> int:
        """Whole seconds a test of measured duration *measured_s* (``None``: never measured) may run."""
        if measured_s is None:
            return min(self.ceiling_s, self.unmeasured_s)
        return min(self.ceiling_s, max(self.floor_s, math.ceil(self.multiple * measured_s)))


@dataclass(frozen=True)
class Builds:
    """The declaration, plus the keys computed for one commit (empty when undeclared)."""

    dependency_inputs: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    native_inputs: tuple[str, ...] = ()
    native_build: str = ''
    install: str = ''
    needs: tuple[str, ...] = ()
    env_key: str = ''
    native_key: str = ''
    #: The declared trained native, ``None`` when the repo declares none.
    trained: Trained | None = None
    #: Which native :attr:`native_key` keys (:data:`NATIVES`) -- :func:`builds_at`'s selection.
    native: str = PLAIN
    #: ``{scope: seconds}`` handed to pytest as ``--timeout``; a scope absent here keeps the repo's own default.
    timeout_s: dict[str, int] = field(default_factory=dict)
    #: Repository paths a job's artifact carries beside the source roots (:mod:`lab_commons.hpc.artifact`).
    artifact_paths: tuple[str, ...] = ()
    #: The repo path of its PRICED FLOORS (:func:`read_priced`), and what that file holds at the commit.
    priced: str = ''
    priced_floors: dict[str, dict[str, float]] = field(default_factory=dict)
    #: The per-test wall the item runner kills a hung test at (:class:`ItemWall`).
    item_wall: ItemWall = field(default_factory=ItemWall)


_TABLE: Final = '[tool.lab_commons.hpc]'
#: ``item_wall``'s duration keys and the :class:`ItemWall` field (whole seconds) each fills; ``multiple`` is a number.
_ITEM_WALL_DURATIONS: Final = {'floor': 'floor_s', 'unmeasured': 'unmeasured_s', 'ceiling': 'ceiling_s'}
_ITEM_WALL_KEYS: Final = frozenset({'multiple', *_ITEM_WALL_DURATIONS})
#: The unit-suffixed spellings the 2026-11-25 ruling retired, refused with their new line; never read.
_ITEM_WALL_RENAMED: Final = {old: Renamed(new, 's') for new, old in _ITEM_WALL_DURATIONS.items()}


def _seconds(key: str, value: object) -> int:
    """*value* (declared as *key*) as positive whole seconds, rounded up; refused as a declaration."""
    try:
        return math.ceil(parse(f'{_TABLE} {key}', value, 'duration', positive=True))
    except QuantityRefused as bad:
        raise InvalidDeclaration(str(bad)) from bad


def _item_wall(raw: dict[str, object]) -> ItemWall:
    """The ``item_wall`` table; a retired or unknown key, a bad value or a floor past the ceiling is refused."""
    try:
        refuse_renamed(f'{_TABLE} item_wall', raw, _ITEM_WALL_RENAMED)
    except QuantityRefused as retired:
        raise InvalidDeclaration(str(retired)) from retired
    multiple = raw.get('multiple', ItemWall.multiple)
    if (
        set(raw) - _ITEM_WALL_KEYS
        or isinstance(multiple, bool)
        or not isinstance(multiple, int | float)
        or multiple <= 0
    ):
        msg = (
            f'{_TABLE} item_wall {raw!r}: keys {sorted(_ITEM_WALL_KEYS)}, multiple a positive number, '
            'the rest durations'
        )
        raise InvalidDeclaration(msg)
    walls = {
        field_name: _seconds(f'item_wall.{key}', raw[key])
        for key, field_name in _ITEM_WALL_DURATIONS.items()
        if key in raw
    }
    wall = ItemWall(multiple=float(multiple), **walls)
    if wall.floor_s > wall.ceiling_s:
        msg = f'{_TABLE} item_wall {raw!r}: floor may not pass ceiling'
        raise InvalidDeclaration(msg)
    return wall


def _timeout(raw: dict[str, object]) -> dict[str, int]:
    """``timeout``: run scope -> pytest's ``--timeout`` in whole seconds; the retired ``timeout_s`` is refused."""
    if 'timeout_s' in raw:
        old = raw['timeout_s']
        scopes = ', '.join(f'{k} = {form(v, "s")}' for k, v in old.items()) if isinstance(old, dict) else '...'
        msg = (
            f'{_TABLE} timeout_s = {old!r} is retired (user ruling 2026-11-25: a value carries its unit, parsed '
            f'once by lab_commons.quantity): write timeout = {{ {scopes} }}'
        )
        raise InvalidDeclaration(msg)
    declared = raw.get('timeout', {})
    if not isinstance(declared, dict) or set(declared) - set(SCOPES):
        msg = f'{_TABLE} timeout {declared!r}: keys are scopes {SCOPES}, values durations such as "5 min"'
        raise InvalidDeclaration(msg)
    return {scope: _seconds(f'timeout.{scope}', value) for scope, value in declared.items()}


#: A price's declared keys -> the floor key it fills (the measured record's: GiB and seconds), its dimension and
#: the base units (bytes, seconds) in one unit of that floor.
_PRICE_KEYS: Final = {'mem': ('mem_gb', 'size', 1024.0**3), 'wall': ('wall_s', 'duration', 1.0)}
_PRICE_RENAMED: Final = {'mem_gb': Renamed('mem', 'GiB'), 'wall_s': Renamed('wall', 's')}


def read_priced(text: str, path: str) -> dict[str, dict[str, float]]:
    """A repo's PRICED FLOORS, the TOML at *path* (``[tool.lab_commons.hpc] priced``), refused by name if malformed.

    Each key is a test node id or a test file, each value ``{ mem = "9.6 GiB", wall = "750 s" }`` (either or both):
    the RAW MEASURED peak memory and wall, from a measurement the file's comments cite, with NO margin. A plan
    reads them as floors (:meth:`lab_commons.compute.measured.Measured.floored`), exactly as it reads a learned
    peak, and applies its ONE safety factor (:attr:`lab_commons.hpc.config.Policy.safety`) -- a declared margin
    on top is the same fact in two places and asks ~2.25x (user ruling 2026-10-31). Round 0 thus asks what a
    host would otherwise learn only by killing it (run 4fb97faa904f).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as broken:
        msg = f'{path}: not TOML ({broken})'
        raise InvalidDeclaration(msg) from broken
    floors: dict[str, dict[str, float]] = {}
    for key, price in data.items():
        if '.py' not in key.split('::', maxsplit=1)[0] or not isinstance(price, dict) or not price:
            msg = f'{path}: {key!r} must be a test file or node id priced as {{ mem = "<n> GiB", wall = "<n> s" }}'
            raise InvalidDeclaration(msg)
        try:
            refuse_renamed(f'{path}: {key!r}', price, _PRICE_RENAMED)
        except QuantityRefused as retired:
            raise InvalidDeclaration(str(retired)) from retired
        if unknown := sorted(set(price) - set(_PRICE_KEYS)):
            msg = f'{path}: {key!r} has unknown key(s) {unknown}; a price is {sorted(_PRICE_KEYS)}'
            raise InvalidDeclaration(msg)
        floors[key] = {}
        for name, value in price.items():
            floor, dimension, per = _PRICE_KEYS[name]
            try:
                floors[key][floor] = parse(f'{path}: {key!r} {name}', value, dimension, positive=True) / per
            except QuantityRefused as bad:
                raise InvalidDeclaration(str(bad)) from bad
    return floors


_TRAINED_KEYS: Final = frozenset({'build', 'cpus', 'mem', 'wall', 'prefetch', 'needs', 'inputs'})
_TRAINED_REQUIRED: Final = ('build', 'cpus', 'mem', 'wall')


def _trained(raw: object) -> Trained | None:
    """``native.trained`` as a :class:`Trained`; absent is ``None``, an unknown or missing key is refused by name."""
    if raw is None:
        return None
    where = f'{_TABLE} native.trained'
    if not isinstance(raw, dict):
        msg = f'{where} must be a table such as {{ build = "...", cpus = 32, mem = "64 GiB", wall = "50 min" }}'
        raise InvalidDeclaration(msg)
    if unknown := sorted(set(raw) - _TRAINED_KEYS):
        msg = f'{where} has unknown keys {unknown}; it holds only {sorted(_TRAINED_KEYS)}'
        raise InvalidDeclaration(msg)
    if missing := [key for key in _TRAINED_REQUIRED if key not in raw]:
        msg = f'{where} misses {missing}: a trained build names its command and the cpus, mem and wall of its job'
        raise InvalidDeclaration(msg)
    cpus = raw['cpus']
    if not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1:
        msg = f'{where} cpus = {cpus!r} must be a whole number of CPUs, at least 1'
        raise InvalidDeclaration(msg)
    try:
        mem_gb = parse(f'{where} mem', raw['mem'], 'size', positive=True) / 1024.0**3
    except QuantityRefused as bad:
        raise InvalidDeclaration(str(bad)) from bad
    return Trained(
        build=str(raw['build']),
        cpus=cpus,
        mem_gb=mem_gb,
        wall_s=_seconds('native.trained.wall', raw['wall']),
        prefetch=str(raw.get('prefetch', '')),
        needs=tuple(raw.get('needs', ())),
        inputs=tuple(raw.get('inputs', ())),
    )


#: The key a declaration may no longer carry, with its door (FORMATS-MIGRATE-ONCE: refused, never read).
RETIRED_INSTALL: Final = (
    '[tool.lab_commons.hpc] install is retired: the venv is `uv sync --frozen` of the extras [tool.dev_lab.dep] '
    'selects for the need-set (lab_commons.extras) -- delete the line and declare extras/needs there'
)


def read_builds(text: str, needs: Iterable[str] = ()) -> Builds:
    """The ``[tool.lab_commons.hpc]`` declaration of pyproject *text* for *needs*; absent is an empty :class:`Builds`.

    The install is derived for *needs* (:func:`lab_commons.extras.sync_command`) wherever the table is declared.
    """
    tables = kit_tables(text)
    raw = tables.get('hpc', {})
    if 'install' in raw:
        raise InvalidDeclaration(RETIRED_INSTALL)
    native = raw.get('native', {})
    builds = Builds(
        needs=tuple(raw.get('needs', ())),
        dependency_inputs=tuple(raw.get('dependency_inputs', ())),
        source_roots=tuple(raw.get('source_roots', ())),
        artifact_paths=tuple(raw.get('artifact_paths', ())),
        native_inputs=tuple(native.get('inputs', ())),
        native_build=str(native.get('build', '')),
        trained=_trained(native.get('trained')),
        timeout_s=_timeout(raw),
        item_wall=_item_wall(dict(raw.get('item_wall', {}))),
        priced=str(raw.get('priced', '')),
    )
    if builds.dependency_inputs and not builds.source_roots:
        msg = '[tool.lab_commons.hpc] dependency_inputs needs source_roots: a shared venv holds no sha source'
        raise InvalidDeclaration(msg)
    if 'hpc' in tables:
        try:
            install = sync_command(tomllib.loads(text), needs, project=not builds.dependency_inputs)
        except (ValueError, TypeError) as undeclared:
            raise InvalidDeclaration(str(undeclared)) from undeclared
        builds = replace(builds, install=install)
    if bool(builds.native_inputs) != bool(builds.native_build):
        msg = '[tool.lab_commons.hpc] native needs both inputs and build'
        raise InvalidDeclaration(msg)
    if builds.trained is not None and not builds.native_inputs:
        msg = f'{_TABLE} native.trained keys on native.inputs: declare them (and the plain native.build beside it)'
        raise InvalidDeclaration(msg)
    return builds


def cargo_target(native_key: str) -> str:
    """Cargo's target dir for the native build keyed *native_key*, relative to the account root.

    A target dir may be shared only by builds of IDENTICAL native inputs: cargo's freshness is mtime-based,
    so one dir shared by every native build reused artifacts compiled from another tree's source.
    Measured 2026-10-14 on Ada: the pybind crate compiled against a stale ``mesh_core`` and every downstream
    deploy was refused. Used by the cluster runs (:mod:`lab_commons.hpc.shell`) and the deploy alike.
    """
    if not native_key:
        msg = 'a cargo target dir needs a native key'
        raise ValueError(msg)
    return f'cache/cargo/{native_key}'


def key_of(oids: dict[str, str], *recipe: str) -> str:
    """The key of a build whose inputs have git object ids *oids*, made by *recipe* (command, interpreter)."""
    text = '\n'.join([*recipe, *(f'{path} {oid}' for path, oid in sorted(oids.items()))])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:_KEY_HEX]


def _oids(repo: Path, sha: str, paths: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        done = _door.run(
            [_GIT, '-C', str(repo), 'rev-parse', '--verify', '-q', f'{sha}:{path.rstrip("/")}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
        out[path] = done.stdout.strip() if done.returncode == 0 else 'absent'
    return out


def builds_at(repo: Path, sha: str, *, python: str, needs: Iterable[str] = (), native: str = PLAIN) -> Builds:
    """The declaration commit *sha* of *repo* carries for *needs*, with its keys -- every git call here only READS.

    *native* selects which native build the key is of (:data:`NATIVES`); the trained one adds its build's needs.

    Raises:
        ValueError: the commit declares no ``[tool.lab_commons.hpc]`` table -- a venv with no build command; or
            *native* is not one of :data:`NATIVES`.
        InvalidDeclaration: *native* is trained and the commit declares no ``native.trained``.

    """
    if native not in NATIVES:
        msg = f'native = {native!r} is not one of {NATIVES}'
        raise ValueError(msg)
    text = _door.run(
        [_GIT, '-C', str(repo), 'show', f'{sha}:pyproject.toml'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    builds = read_builds(text, needs)
    if native == TRAINED:
        if builds.trained is None:
            msg = (
                f'{sha[:12]} declares no {_TABLE} native.trained, so a job cannot select native = "trained": declare '
                'it beside native.build (lab_commons.hpc.builds.Trained) or run the plain build'
            )
            raise InvalidDeclaration(msg)
        builds = replace(read_builds(text, (*needs, *builds.trained.needs)), native=TRAINED)
    if not builds.install:
        msg = f'{sha[:12]} declares no [tool.lab_commons.hpc] table: the cluster builds no venv for it'
        raise ValueError(msg)
    if builds.priced:
        shown = _door.run(
            [_GIT, '-C', str(repo), 'show', f'{sha}:{builds.priced}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
        if shown.returncode:
            msg = f'{sha[:12]} declares [tool.lab_commons.hpc] priced = {builds.priced!r}, which it does not carry'
            raise InvalidDeclaration(msg)
        builds = replace(builds, priced_floors=read_priced(shown.stdout, builds.priced))
    oids = _oids(repo, sha, builds.dependency_inputs) if builds.dependency_inputs else {'commit': sha}
    env_key = key_of(oids, builds.install, python, *builds.needs)
    native_key = ''
    if builds.native_inputs:
        trained = builds.trained if native == TRAINED else None
        recipe = (builds.native_build,) if trained is None else (TRAINED, trained.prefetch, trained.build)
        inputs = builds.native_inputs if trained is None else (*builds.native_inputs, *trained.inputs)
        native_key = key_of(_oids(repo, sha, inputs), *recipe, python, env_key)
    return replace(builds, env_key=env_key, native_key=native_key)
