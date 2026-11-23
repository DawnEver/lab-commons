"""HEADROOM -- what a cache may still write: its volume's free space, capped by the account's QUOTA where one binds.

``shutil.disk_usage`` answers for the whole volume. On a shared filesystem an account's quota can be nearly full
while the volume shows terabytes free, and a quota may also limit FILES. So a machine whose caches sit under a
quota declares how to read it -- ``[storage] quota = { probe = [argv...], format = "<name>" }`` in its machine file
(:mod:`lab_commons.storage`) -- and the floor (:mod:`lab_commons.compute.floor`) holds against the smaller of the two.

A declared probe that cannot be run, fails, or prints what its format cannot read is refused by name
(:class:`QuotaUnreadable`): it never falls back to the volume, which is exactly the number that lied. A machine that
declares no quota (a workstation) is measured by its volume alone.

Formats (:data:`FORMATS`), each a real tool's plain output for ONE filesystem:

* ``mmlsquota`` -- GPFS ``mmlsquota``; block counts are in the unit of the probe's ``--block-size`` (a byte count or
  ``K``/``M``/``G``/``T``; KiB when absent; ``auto`` prints suffixed values and is refused).
* ``quota-w`` -- Linux ``quota -w``; block counts in KiB (``-s``/``--human-readable`` is refused).

A limit of 0 means none; the soft quota binds before the hard limit, so the smaller non-zero of the two is the limit.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ['FORMATS', 'Floor', 'Quota', 'QuotaUnreadable', 'Usage', 'read']

_UNITS = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
_TIMEOUT_S = 60


@dataclass(frozen=True)
class Quota:
    """How a machine reads its account's quota: the *probe* argv and the *format* naming its output's parser."""

    probe: tuple[str, ...]
    format: str


@dataclass(frozen=True)
class Floor:
    """What every regenerable cache leaves free: *bytes*, and *files* where a quota limits them.

    *files* ``None`` with a file-limited quota keeps 1% of that limit. *quota* ``None``: the volume alone.
    """

    bytes: int
    files: int | None = None
    quota: Quota | None = None


@dataclass(frozen=True)
class Usage:
    """A quota read: the bytes and files still writable before a limit (``None``: unlimited) and the file limit."""

    bytes_left: int | None
    files_left: int | None
    files_limit: int | None


class QuotaUnreadable(OSError):
    """A declared quota probe that could not be run or read: never replaced by the volume's free space."""

    def __init__(self, quota: Quota, cause: str) -> None:
        """Name the probe, its format and *cause*."""
        self.quota, self.cause = quota, cause
        msg = (
            f'[storage] quota probe {list(quota.probe)} (format {quota.format!r}): {cause} -- the cache floor cannot '
            'hold against an unread quota; fix [storage] quota in the machine file (lab_commons.storage), or '
            'delete it if this machine has no quota'
        )
        super().__init__(msg)


def _limit(soft: int, hard: int) -> int | None:
    """The binding limit: the smaller non-zero of *soft* and *hard*; ``None`` when both are 0 (no limit)."""
    set_ = [one for one in (soft, hard) if one > 0]
    return min(set_) if set_ else None


def _usage(blocks: tuple[int, int, int], unit: int, files: tuple[int, int, int]) -> Usage:
    """A usage from ``(used, soft, hard)`` *blocks* of *unit* bytes and ``(used, soft, hard)`` *files*."""
    byte_limit, file_limit = _limit(*blocks[1:]), _limit(*files[1:])
    return Usage(
        None if byte_limit is None else max(0, (byte_limit - blocks[0]) * unit),
        None if file_limit is None else max(0, file_limit - files[0]),
        file_limit,
    )


def _one(rows: list[str], quota: Quota) -> str:
    if len(rows) != 1:
        cause = f'it printed {len(rows)} filesystem rows; narrow the probe to the filesystem holding the cache'
        raise QuotaUnreadable(quota, cause)
    return rows[0]


def _ints(tokens: Sequence[str], count: int, quota: Quota, row: str) -> list[int]:
    found = [int(one.rstrip('*')) for one in tokens if one.rstrip('*').isdigit()]
    if len(found) < count:
        raise QuotaUnreadable(quota, f'row {row.strip()!r} carries fewer than {count} counts')
    return found[:count]


def _block_size(quota: Quota) -> int:
    argv = list(quota.probe)
    if '--block-size' not in argv:
        return _UNITS['K']
    at = argv.index('--block-size') + 1
    value = argv[at].upper() if at < len(argv) else ''
    if value.isdigit() and int(value) > 0:
        return int(value)
    if value in _UNITS:
        return _UNITS[value]
    raise QuotaUnreadable(
        quota, f'--block-size {value or "(missing)"} prints suffixed counts: give a byte count or K/M/G/T'
    )


def _mmlsquota(text: str, quota: Quota) -> Usage:
    unit = _block_size(quota)
    rows = [line for line in text.splitlines() if '|' in line and not re.search(r'Filesystem|Block Limits', line)]
    row = _one(rows, quota)
    blocks, files = row.split('|', 1)
    used, soft, hard = _ints(blocks.split()[2:], 3, quota, row)
    nfiles, fsoft, fhard = _ints(files.split(), 3, quota, row)
    return _usage((used, soft, hard), unit, (nfiles, fsoft, fhard))


def _quota_w(text: str, quota: Quota) -> Usage:
    if {'-s', '--human-readable'} & set(quota.probe):
        raise QuotaUnreadable(quota, 'human-readable counts (-s) cannot be read: drop -s from the probe')
    lines = [line for line in text.splitlines() if line.strip()]
    rows = [line for line in lines if not re.match(r'\s*(Disk quotas for|Filesystem\b)', line)]
    if not rows and any(line.rstrip().endswith(': none') for line in lines):
        return Usage(None, None, None)
    row = _one(rows, quota)
    used, soft, hard, nfiles, fsoft, fhard = _ints(row.split()[1:], 6, quota, row)
    return _usage((used, soft, hard), _UNITS['K'], (nfiles, fsoft, fhard))


#: The named quota formats a machine may declare (``[storage] quota.format``).
FORMATS: dict[str, Callable[[str, Quota], Usage]] = {'mmlsquota': _mmlsquota, 'quota-w': _quota_w}


def read(quota: Quota, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> Usage:
    """Run *quota*'s probe and read its output by its format.

    Raises:
        QuotaUnreadable: the format is unknown, the probe cannot run or fails, or its output is unreadable.

    """
    parse = FORMATS.get(quota.format)
    if parse is None:
        raise QuotaUnreadable(quota, f'the format is not one of {sorted(FORMATS)}')
    try:
        done = run(list(quota.probe), capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QuotaUnreadable(quota, f'it could not run ({exc})') from exc
    if done.returncode != 0:
        raise QuotaUnreadable(quota, f'it exited {done.returncode}: {(done.stderr or "").strip()[:200]}')
    return parse(done.stdout, quota)
