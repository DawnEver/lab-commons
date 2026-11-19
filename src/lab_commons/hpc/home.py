"""THE CLUSTER HOME, as facts: its quota, the thresholds it is held to, the roots that own it, and its census.

INCIDENT 2026-11-05/06 (Ada): the home GPFS file quota (4,040,000 inodes) filled. ``~/ab`` alone held 2.3 M
files -- 69 full worktrees of one repository, ~23 k files each, plus per-tree uv caches and cargo targets --
built by hand over ``ssh`` by agent lanes for A/B, bisect and census work. Nothing owned ``~/ab``, so nothing
reaped it; once the quota was full the forge cron could not even write its own log. Three facts were missing
and are declared here, ONCE:

* :data:`QUOTA` -- the thresholds. At ``soft`` a door reaps expired leases first and warns; at ``hard`` it
  REFUSES new trees and submissions by name (:mod:`lab_commons.hpc.homedoor`).
* :data:`MANAGED` -- the home directories that have an OWNER, each with who reaps or bounds it. Any other
  directory past :data:`STRAY_FILES` files is a DEFECT the tick names with its remedy (:func:`strays`),
  the cluster analogue of a stray file in a tree.
* :class:`Usage` -- the quota read off the filesystem. The FRACTION is taken against the SOFT quota where one
  is set, never the hard limit: GPFS refuses writes at the soft quota once its grace runs out (Ada's grace is
  ``none``: measured 2026-11-06), so the soft quota is the ceiling that actually binds.

Everything here is a shell string or a parser; nothing runs a command. Measured fixtures:
``tests/_hpc_fixtures/ada-*-2026-11-06.txt``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    'CENSUS_PREFIX',
    'MANAGED',
    'QUOTA',
    'QUOTA_SCRIPT',
    'STRAY_FILES',
    'Consumer',
    'Thresholds',
    'Usage',
    'census_script',
    'parse_census',
    'parse_mmlsquota',
    'parse_quota',
    'parse_usage',
    'stray_text',
    'strays',
]


@dataclass(frozen=True)
class Thresholds:
    """Fractions of the binding quota: at ``soft`` reap then warn; at ``hard`` refuse new trees and submissions."""

    soft: float
    hard: float

    def __post_init__(self) -> None:
        """``0 < soft < hard < 1``: a refusal must leave room to reap, and a warning must come before it."""
        if not 0 < self.soft < self.hard < 1:
            msg = f'thresholds need 0 < soft < hard < 1, got {self}'
            raise ValueError(msg)


#: THE ONE DECLARATION of the home quota thresholds; every door and the tick read it.
QUOTA: Final = Thresholds(soft=0.80, hard=0.90)

#: Home directories with an OWNER, each naming it. A deploy root (``[deploy] root``) is passed in by its owner.
MANAGED: Final[Mapping[str, str]] = {
    'ci': 'lab_commons.hpc: run trees, envs and leased trees; reaped by forge retention and the lease reaper',
    '.cache': 'tool caches (uv, the tick log); bounded by the tools that write them',
    '.cargo': 'the Rust toolchain install',
    '.rustup': 'the Rust toolchain install',
    '.local': 'user-installed tools (uv, python)',
}

#: A directory outside :data:`MANAGED` holding more files than this is a stray: no reaper will ever bound it.
#: One worktree of a large repository is ~23 k files (Ada 2026-11-05), so two are already a stray.
STRAY_FILES: Final = 50_000

_SOURCE: Final = '@@@ fs '
_LINUX: Final = '@@@ quota-w'

#: Prints the home's filesystem, ``mmlsquota -Y`` (GPFS, also at its absolute path: cron's PATH lacks it), then
#: ``quota -w``. ``mmlsquota`` exits non-zero on Ada while printing the right row (other filesystems it cannot
#: read, measured 2026-11-06), so no exit status is trusted -- only the rows.
QUOTA_SCRIPT: Final = (
    f'printf "{_SOURCE}%s\\n" "$(df -P "$HOME" 2>/dev/null | awk \'NR==2{{print $1}}\')"; '
    'for m in mmlsquota /usr/lpp/mmfs/bin/mmlsquota; do '
    'if command -v "$m" >/dev/null 2>&1; then "$m" -Y 2>/dev/null; break; fi; done; '
    f'echo "{_LINUX}"; quota -w 2>/dev/null; true'
)


@dataclass(frozen=True)
class Usage:
    """Bytes and files held against their quota; ``files`` is ``None`` where no file quota is read."""

    used: int
    limit: int
    files: int | None
    files_limit: int | None

    def fraction(self, freed: int = 0, freed_files: int = 0) -> float:
        """The nearer of the two ceilings, as a fraction, after *freed* bytes and *freed_files* files."""
        parts = [(self.used - freed) / self.limit] if self.limit else []
        if self.files is not None and self.files_limit:
            parts.append((self.files - freed_files) / self.files_limit)
        return max(parts, default=0.0)

    @property
    def files_left(self) -> float | None:
        """Files the quota still takes, never below 0; ``None`` when no file quota is read."""
        if self.files is None or not self.files_limit:
            return None
        return float(max(0, self.files_limit - self.files))

    def binding(self, freed: int = 0, freed_files: int = 0) -> str:
        """``'files'`` or ``'bytes'``: the ceiling :meth:`fraction` is nearer to."""
        if self.files is None or not self.files_limit:
            return 'bytes'
        files = (self.files - freed_files) / self.files_limit
        return 'files' if files >= ((self.used - freed) / self.limit if self.limit else 0.0) else 'bytes'

    def after(self, freed: int, freed_files: int) -> list[str]:
        """``usage bytes ... -> ...`` and, with a file quota, ``usage files ... -> ...``: now, then once freed."""
        gb, left = 1024**3, self.used - freed
        lines = [f'usage bytes {self.used / gb:.1f}/{self.limit / gb:.1f} GB -> {left / gb:.1f} GB']
        if self.files is not None and self.files_limit:
            now, rest, cap = self.files, self.files - freed_files, self.files_limit
            lines.append(f'usage files {now}/{cap} ({now / cap:.1%}) -> {rest} ({rest / cap:.1%})')
        return lines

    def text(self) -> str:
        """``bytes <used>/<limit> GB, files <used>/<limit>``; files ``unread`` when no file quota is read."""
        gb = 1024**3
        files = f'{self.files}/{self.files_limit}' if self.files is not None and self.files_limit else 'unread'
        return f'bytes {self.used / gb:.1f}/{self.limit / gb:.1f} GB, files {files}'


# the block grace is ``none`` or ``7 days`` -- one word or two -- so it is read up to the bar
_QUOTA_ROW: Final = re.compile(r'^\S+\s+\S+\s+USR\s+(\d+)\s+(\d+)\s+\d+\s+\d+[^|\n]*\|\s*(\d+)\s+(\d+)', re.MULTILINE)
# ``quota -w``: filesystem, KB used (``*`` when over), quota, limit, [grace], files used, quota, limit
_LINUX_ROW: Final = re.compile(r'^\s*/\S+\s+(\d+)\*?\s+(\d+)\s+\d+\s+(?:\S+\s+)??(\d+)\*?\s+(\d+)\s+\d+', re.MULTILINE)


def _usage(hit: re.Match[str] | None) -> Usage | None:
    if hit is None:
        return None
    used, limit, files, files_limit = (int(v) for v in hit.groups())
    return Usage(used * 1024, limit * 1024, files, files_limit)


def parse_mmlsquota(text: str) -> Usage | None:
    """The ``USR`` row of human ``mmlsquota`` (KB blocks, then files) against the soft quota; ``None`` when none."""
    return _usage(_QUOTA_ROW.search(text))


def parse_quota(text: str) -> Usage | None:
    """The first filesystem row of Linux ``quota -w`` (KB blocks, then files); ``None`` when there is none."""
    return _usage(_LINUX_ROW.search(text))


def _ceiling(quota: str, limit: str) -> int:
    """The soft quota where one is set (it binds once grace is out), else the hard limit."""
    return int(quota) if quota.isdigit() and int(quota) else int(limit) if limit.isdigit() else 0


def _parse_y(text: str, source: str) -> Usage | None:
    """The ``USR`` row of ``mmlsquota -Y`` on *source* (else the first ``USR`` row), read by its header's names."""
    header: list[str] = []
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith('mmlsquota:'):
            continue
        cells = line.split(':')  # 'mmlsquota: Command failed.' -- Ada prints it beside the row -- is too short
        if cells[2:3] == ['HEADER']:
            header = cells
        elif header and len(cells) >= len(header) - 1:
            row = dict(zip(header, cells, strict=False))
            if row.get('quotaType') == 'USR' and row.get('blockUsage', '').isdigit():
                rows.append(row)
    if not rows:
        return None
    row = next((r for r in rows if r.get('filesystemName') == source.rsplit('/', 1)[-1]), rows[0])
    return Usage(
        int(row['blockUsage']) * 1024,
        _ceiling(row['blockQuota'], row['blockLimit']) * 1024,
        int(row['filesUsage']) if row['filesUsage'].isdigit() else None,
        _ceiling(row['filesQuota'], row['filesLimit']) or None,
    )


def parse_usage(text: str) -> Usage | None:
    """What :data:`QUOTA_SCRIPT` printed: ``mmlsquota -Y``, else human ``mmlsquota``, else ``quota -w``; ``None``."""
    head, _, linux = text.partition(_LINUX)
    source = next((line.removeprefix(_SOURCE).strip() for line in head.splitlines() if line.startswith(_SOURCE)), '')
    return _parse_y(head, source) or parse_mmlsquota(head) or parse_quota(linux)


#: Each census line: ``@@@ dir <name>\t<files, capped>``.
CENSUS_PREFIX: Final = '@@@ dir '


def census_script(cap: int = STRAY_FILES + 1) -> str:
    """Count each top-level home directory's entries, STOPPING at *cap*: the cost is bounded, never a full walk.

    A full ``du --inodes`` of a 3 M-file GPFS home did not finish in 280 s (Ada 2026-11-06); ``find | head``
    stops each walk at *cap* (``find`` dies of SIGPIPE), so a census costs at most *cap* entries per directory.
    Symlinks are not followed and the walk stays on the home filesystem (``-xdev``).
    """
    return (
        'cd "$HOME" 2>/dev/null || exit 0; '
        'for d in * .[!.]* ..?*; do [ -d "$d" ] && [ ! -L "$d" ] || continue; '
        f'n=$(find "$d" -xdev 2>/dev/null | head -n {int(cap)} | wc -l); '
        f'printf "{CENSUS_PREFIX}%s\\t%s\\n" "$d" "$n"; done; true'
    )


@dataclass(frozen=True)
class Consumer:
    """One top-level home directory and its file count; ``capped`` when the census stopped counting it."""

    name: str
    files: int
    capped: bool

    def text(self) -> str:
        """``~/<name>: <n> files`` -- ``>= <n>`` when capped."""
        return f'~/{self.name}: {">= " if self.capped else ""}{self.files} files'


def parse_census(text: str, cap: int = STRAY_FILES + 1) -> list[Consumer]:
    """The census's directories, most files first."""
    found = []
    for line in text.splitlines():
        if line.startswith(CENSUS_PREFIX) and '\t' in line:
            name, _, count = line.removeprefix(CENSUS_PREFIX).rpartition('\t')
            if count.strip().isdigit():
                files = int(count)
                found.append(Consumer(name, files, files >= cap))
    return sorted(found, key=lambda c: (-c.files, c.name))


def strays(consumers: Iterable[Consumer], managed: Iterable[str] = (), limit: int = STRAY_FILES) -> list[Consumer]:
    """Each directory outside :data:`MANAGED` (and the extra *managed* roots) past *limit* files."""
    owned = set(MANAGED) | set(managed)
    return [c for c in consumers if c.name not in owned and c.files > limit]


def stray_text(found: Iterable[Consumer]) -> list[str]:
    """``STRAY-HOME-TREE`` lines: each stray, and the remedy -- the lease door, then the named delete door."""
    return [
        f'STRAY-HOME-TREE: {c.text()} outside the managed roots ({", ".join(sorted(MANAGED))}); no reaper bounds it. '
        'Ad-hoc trees go through `python -m lab_commons.hpc tree acquire` (a lease the tick reaps); '
        f'once nothing uses it, remove it through `python -m lab_commons.hpc clean --root <home> {c.name}` '
        '(refused while a job runs under it)'
        for c in found
    ]
