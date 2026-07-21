"""App-agnostic path resolution + per-run output directories.

Provenance: extracted from motronics-studio's ``core/utils/config.py``, parameterized by
``app_name`` -- the motronics original already derived the home env var and the
platformdirs namespace from ``app_name`` instead of hardcoding ``'motronics'`` /
``MOTRONICS_HOME``, so this extraction is a pure relocation, not a redesign.

Home-resolution precedence (per app):
  1. ``<APP>_HOME`` env (or an explicit ``env_var``); an explicit override.
  2. Auto-detected source checkout -> ``<repo>``. Makes dev/worktree runs default
     in-repo with no env needed.
  3. Platform-standard dirs (only when installed as a third-party pip package).
"""

import datetime
import os
from pathlib import Path

from platformdirs import user_cache_path, user_config_path

__all__ = [
    'config_root',
    'output_root',
    'resolve_home',
    'run_date',
    'run_output_dir',
    'run_stamp',
    'unique_run_dir',
]


def _detect_repo_root(start: Path | None = None) -> Path | None:
    """Return the repo root when running from a source checkout, else None.

    Source layout is ``<root>/src/<pkg>/.../<file>``; a pip-installed wheel sits in
    site-packages with no enclosing ``src/`` + ``pyproject.toml``, so detection fails
    cleanly and the caller falls back to platform dirs. Editable installs
    (``pip install -e``) keep the source tree, so they detect as dev too.

    Args:
        start(Path | None): Starting path for the upward search. Defaults to this file.

    Returns:
        Path | None: The repository root, or None when not in a source checkout.

    """
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if parent.name == 'src' and (parent.parent / 'pyproject.toml').is_file():
            return parent.parent
    return None


def resolve_home(app_name: str, *, env_var: str | None = None) -> Path | None:
    """Return the explicit or auto-detected home root for ``app_name``, else None.

    ``env_var`` defaults to ``<APP_NAME>_HOME`` (upper-cased); pass it explicitly to
    honor a differently-named or deprecated variable. Returns None when installed as a
    third-party package with no home env set (the platformdirs fallback lives in
    :func:`output_root` / :func:`config_root`).
    """
    env_var = env_var or f'{app_name.upper()}_HOME'
    home = os.environ.get(env_var)
    if home:
        return Path(home).expanduser().resolve()
    return _detect_repo_root()


def output_root(app_name: str, *, env_var: str | None = None) -> Path:
    """The user-writable output root: ``<home>/output`` or platformdirs cache dir."""
    home = resolve_home(app_name, env_var=env_var)
    return (home / 'output') if home is not None else user_cache_path(app_name)


def config_root(app_name: str, *, env_var: str | None = None) -> Path:
    """The user-writable config root: ``<home>/config`` or platformdirs config dir."""
    home = resolve_home(app_name, env_var=env_var)
    return (home / 'config') if home is not None else user_config_path(app_name)


# The per-run wall-clock stamp, computed ONCE on first use and REUSED for every
# artifact of the same process (all plots, logs, reports land in the SAME run folder).
# ``None`` until the first :func:`run_stamp` call materializes it. Filesystem-safe
# ``HH-MM-SS`` (colons are invalid on Windows). Memoizing it here -- not per caller --
# means every callsite gets the same run folder with NO caller changes. ``_now_stamp``
# is the injectable clock so a deterministic test can patch the wall-clock nondeterminism.
_run_stamp: str | None = None


def _now_stamp() -> str:
    """The current wall-clock time as a filesystem-safe ``HH-MM-SS`` (patchable in tests)."""
    return datetime.datetime.now().strftime(r'%H-%M-%S')


def run_stamp() -> str:
    """The process-wide per-run timestamp (``HH-MM-SS``), computed once and cached.

    Stamped ONCE on first use and reused for the rest of the process, so every artifact of
    a single invocation shares one run folder. Patch :func:`_now_stamp` (or reset the
    module-level ``_run_stamp``) to make it deterministic in a test.
    """
    global _run_stamp  # noqa: PLW0603
    if _run_stamp is None:
        _run_stamp = _now_stamp()
    return _run_stamp


_run_date: tuple[str, str, str] | None = None


def _now_date() -> tuple[str, str, str]:
    """Current date as ``(yy, mm, dd)`` filesystem segments (patchable in tests)."""
    now = datetime.datetime.now()
    return now.strftime(r'%y'), now.strftime(r'%m'), now.strftime(r'%d')


def run_date() -> tuple[str, str, str]:
    """The process-wide run date as ``(yy, mm, dd)``, computed once and cached.

    Memoized like :func:`run_stamp` so every artifact of one invocation shares the same
    ``<yy>/<mm>/<dd>`` folder even if the run straddles midnight. Patch :func:`_now_date`
    (or reset ``_run_date``) for a deterministic test.
    """
    global _run_date  # noqa: PLW0603
    if _run_date is None:
        _run_date = _now_date()
    return _run_date


def run_output_dir(app_name: str, name: str | None = None, *, root: Path | None = None) -> Path:
    r"""Return (creating) the per-run output dir ``<root>/logs/<yy>/<mm>/<dd>/<name>/<HH-MM-SS>/``.

    The single sanctioned sink for a run's artifacts (plots, regenerated goldens, reports,
    field frames), so runs stop scattering stray dirs at arbitrary CWDs. ``name`` is
    flattened to a single path segment (any ``/`` or ``\\`` becomes ``_``) so it can never
    escape the run root.

    The date is a nested ``<yy>/<mm>/<dd>`` tree (memoized :func:`run_date`) and a per-run
    ``HH-MM-SS`` subfolder (memoized :func:`run_stamp`) is appended so two runs of the SAME
    case no longer CLOBBER each other. Both are computed once per process and reused, so
    EVERY artifact of a single invocation lands in the SAME folder — this memoization is
    why no caller needs changing.

    ``root`` overrides the resolved :func:`output_root` (a consumer with its own,
    already-resolved output root — e.g. motronics honoring a deprecated env var — passes it
    in); when None the root is resolved from ``app_name``.
    """
    base = root if root is not None else output_root(app_name)
    safe = (name or '').replace('\\', '/').replace('/', '_').strip() or 'run'
    path = base.joinpath('logs', *run_date(), safe, run_stamp())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_scoped_stem(stem: str) -> str:
    r"""Flatten ``stem`` to one path segment and append the per-run ``HH-MM-SS`` stamp.

    ``<stem>-<HH-MM-SS>`` — the single human-readable, run-scoped naming scheme every
    collision-safe artifact resolves through. ``stem`` is flattened (any ``/`` or ``\\``
    becomes ``_``) so a case/model name can never escape its parent.
    """
    safe = stem.replace('\\', '/').replace('/', '_').strip() or 'run'
    return f'{safe}-{run_stamp()}'


def unique_run_dir(parent: Path, stem: str) -> Path:
    r"""Atomically create and return a unique, human-readable run subdir under ``parent``.

    The ONE collision-safe path primitive shared by every source that needs a distinct
    run-scoped location in a possibly-SHARED ``parent`` (the FEMM scratch dir, a saved-mesh
    dir, any cached / multi-worktree / concurrent reuse of a fixed stem). The name is
    ``<stem>-<HH-MM-SS>`` from the memoized :func:`run_stamp`, with a short monotonic counter
    suffix (``-2``, ``-3``, ...) appended ONLY on collision.

    Collision safety is race-free across processes: each candidate is claimed with an atomic
    ``mkdir(exist_ok=False)``, so two concurrent creators (the serial FEMM lane, separate
    worktrees sharing one ``parent``) never resolve to the same directory — the loser sees
    ``FileExistsError`` and advances to the next counter.
    """
    parent.mkdir(parents=True, exist_ok=True)
    base = _run_scoped_stem(stem)
    n = 1
    while True:
        candidate = parent / (base if n == 1 else f'{base}-{n}')
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            n += 1
