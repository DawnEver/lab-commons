"""THE PLATFORM SPLIT -- which platforms can run a test, declared ONCE per marker in the repo's pyproject.

::

    [tool.lab_commons.platforms]
    femm = ["windows"]            # a marker -> the platforms that can run a test carrying it
    matlab = ["windows", "linux"]

A marker the table does not list runs everywhere. A test carrying several listed markers runs only
where ALL of them can. A commit's verdict may then be COMPOSED of one part per platform
(:mod:`lab_commons.dev.platformparts`): each part runs what its platform can and no earlier part ran.

The table is read AT THE COMMIT being judged (``git show <sha>:pyproject.toml``), never from the
working tree, so a part is selected by the declaration the commit itself carries. Standard library
only, like the rest of :mod:`lab_commons.hpc`.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Final

__all__ = ['PLATFORMS', 'cannot_run', 'host_platform', 'read_table', 'table_at']

#: The platform names a part is named after -- what it ran ON, nothing else.
PLATFORMS: Final = ('linux', 'windows', 'macos')

_GIT: Final = shutil.which('git') or 'git'

_BY_SYS: Final = {'linux': 'linux', 'win32': 'windows', 'darwin': 'macos'}


def host_platform(sys_platform: str) -> str:
    """The :data:`PLATFORMS` name of a ``sys.platform`` value; an unknown one is refused, never guessed."""
    try:
        return _BY_SYS[sys_platform]
    except KeyError:
        msg = f'no platform part is named for sys.platform={sys_platform!r}; known: {sorted(_BY_SYS)}'
        raise ValueError(msg) from None


def read_table(text: str) -> dict[str, tuple[str, ...]]:
    """``{marker: platforms}`` from pyproject *text*; absent is empty, an unknown platform is refused."""
    raw = tomllib.loads(text).get('tool', {}).get('lab_commons', {}).get('platforms', {})
    table: dict[str, tuple[str, ...]] = {}
    for marker, platforms in raw.items():
        unknown = sorted(set(platforms) - set(PLATFORMS))
        if not isinstance(platforms, list) or unknown:
            msg = f'[tool.lab_commons.platforms] {marker} = {platforms!r}: platforms are a list drawn from {PLATFORMS}'
            raise ValueError(msg)
        table[marker] = tuple(platforms)
    return table


def table_at(repo: Path, sha: str) -> dict[str, tuple[str, ...]]:
    """The table as commit *sha* of *repo* declares it."""
    text = subprocess.run(
        [_GIT, '-C', str(repo), 'show', f'{sha}:pyproject.toml'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
    ).stdout
    return read_table(text)


def cannot_run(table: dict[str, tuple[str, ...]], platform: str) -> str:
    """The marker expression selecting every test *platform* cannot run; ``''`` when it can run all."""
    if platform not in PLATFORMS:
        msg = f'unknown platform {platform!r}; known: {PLATFORMS}'
        raise ValueError(msg)
    return ' or '.join(sorted(marker for marker, can in table.items() if platform not in can))
