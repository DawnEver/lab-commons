"""IS THIS ACCOUNT'S RCLONE REMOTE USABLE -- one cheap, read-only probe, and the recipe that makes it so.

Several HPC accounts write the one shared artefact library, each through its OWN rclone remote
(:mod:`lab_commons.dev.artefacts`). :func:`probe` runs ``rclone lsd <name>: --max-depth 1`` and returns why
the remote is missing or unusable, ``''`` when it answers. :func:`declared_remote` reads the remote a repo
declares for ``backend = "rclone"``. Both live here, not in ``lab_commons.dev``, so ``hpc deploy`` can ask
without reaching the dev layer; the artefact store's own validation stays in :mod:`lab_commons.dev.artefacts`.
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import lab_commons.launch as _door

__all__ = ['RECIPE', 'declared_remote', 'probe']

_TIMEOUT_S: Final = 60

#: How a writer creates its remote; the full recipe is docs-src/dev/artefacts.md.
RECIPE: Final = (
    'create it with `rclone config` (type onedrive, token from `rclone authorize "onedrive"` on a machine with '
    'a browser, the SharePoint site\'s "Documents" library, drive_type = documentLibrary), then chmod 600 the '
    'config; see docs-src/dev/artefacts.md'
)


def declared_remote(root: Path) -> str:
    """The ``remote`` of *root*'s ``[tool.lab_commons.artefacts]`` when its backend is rclone, else ``''``."""
    pyproject = root / 'pyproject.toml'
    raw = tomllib.loads(pyproject.read_text(encoding='utf-8')) if pyproject.is_file() else {}
    table = raw.get('tool', {}).get('lab_commons', {}).get('artefacts', {})
    return str(table.get('remote', '')) if table.get('backend') == 'rclone' else ''


def probe(remote: str, *, rclone: Sequence[str] = ('rclone',)) -> str:
    """Why rclone *remote* (``name:path``) cannot be used from here, ``''`` when its root lists."""
    name = remote.partition(':')[0]
    try:
        done = _door.run(
            [*rclone, 'lsd', f'{name}:', '--max-depth', '1'], capture_output=True, text=True, encoding='utf-8',
            errors='replace', check=False, timeout=_TIMEOUT_S,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as why:
        return f'rclone cannot run here ({type(why).__name__}): {RECIPE}'
    if done.returncode == 0:
        return ''
    tail = done.stderr.strip().splitlines()[-1:] or [f'exit {done.returncode}']
    return f'the rclone remote {name}: is missing or unusable ({tail[0][-160:]}): {RECIPE}'
