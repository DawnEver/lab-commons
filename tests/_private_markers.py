"""The scan behind `test_no_tracked_file_carries_private_markers.py`.

Kept apart so the planted positives and negatives exercise exactly the code the tree check runs.

THIS REPO IS PUBLISHED. A tracked file must not carry a machine, a person, a home directory or a
chat id. Two layers:

* GENERIC patterns, committed here, that need no knowledge of whose machine this is: absolute home
  paths not written as a ``<placeholder>``, e-mail addresses outside the reserved example domains,
  Telegram supergroup ids, and an ``OneDrive - <Org>`` folder name.
* An optional MACHINE-LOCAL denylist at ``~/.claude/private-markers`` -- one case-insensitive
  literal or ``/regex/`` per line, ``#`` comments. It names the private values themselves, so it is
  never committed; absent, the layer is silent.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

#: Domains an address may use: the RFC 2606 / 6761 reserved names and GitHub's noreply alias.
_ALLOWED_MAIL = re.compile(
    r'(?:^|\.)(?:example\.com|example\.org|example\.net|users\.noreply\.github\.com)$|\.(?:invalid|example|test)$',
    re.IGNORECASE,
)

GENERIC: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ('windows home path', re.compile(r'\b[A-Za-z]:[\\/]+Users[\\/]+(?![<$%{])[^\\/\s\'"`]+', re.IGNORECASE)),
    ('posix home path', re.compile(r'(?<![\w.])/(?:Users|home)/(?![<$%{])[A-Za-z0-9._-]+')),
    ('telegram chat id', re.compile(r'-100\d{9,}')),
    ('cloud folder', re.compile(r'OneDrive[ ]-[ ](?!<)\S')),
)

_MAIL = re.compile(r'(?<![\\\w.%+-])[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b')

#: The default location of the machine-local denylist; shared with the sibling cc-config check.
MARKERS_FILE: Final = Path.home() / '.claude' / 'private-markers'


def load_markers(path: Path = MARKERS_FILE) -> tuple[re.Pattern[str], ...]:
    """Compile the denylist at *path*; an absent file is an empty denylist, never an error."""
    if not path.is_file():
        return ()
    out: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if len(line) > 2 and line.startswith('/') and line.endswith('/'):
            out.append(re.compile(line[1:-1], re.IGNORECASE))
        else:
            out.append(re.compile(re.escape(line), re.IGNORECASE))
    return tuple(out)


#: The one place each file NAMES ITS AUTHOR on purpose: the copyright line and the package author.
#: The denylist is skipped on exactly these lines -- keyed by path and line shape, so the exemption
#: never has to spell the name it admits. Generic patterns (an author e-mail) still apply here.
AUTHORSHIP: Final[dict[str, re.Pattern[str]]] = {
    'LICENSE': re.compile(r'^Copyright \(c\) \d{4} [^@]+$'),
    'pyproject.toml': re.compile(r'^authors = \[\{ name = "[^"]+" \}\]$'),
}


def scan_text(text: str, markers: tuple[re.Pattern[str], ...] = (), *, path: str = '') -> list[tuple[int, str, str]]:
    """Every ``(line, kind, match)`` in *text*. A private-denylist hit reports its kind only."""
    hits: list[tuple[int, str, str]] = []
    authorship = AUTHORSHIP.get(path)
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in GENERIC:
            hits.extend((number, kind, m.group(0)) for m in pattern.finditer(line))
        hits.extend((number, 'email', m.group(0)) for m in _MAIL.finditer(line) if not _ALLOWED_MAIL.search(m.group(1)))
        # The matched private value is never echoed: a log of the check must not leak what it guards.
        if authorship is not None and authorship.match(line):
            continue
        hits.extend((number, 'private marker', '<redacted>') for p in markers if p.search(line))
    return hits


def tracked_files(root: Path) -> list[Path]:
    """The files git tracks under *root* -- exactly what a push publishes."""
    done = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True)  # noqa: S607 -- git on PATH
    return [root / name for name in done.stdout.decode('utf-8').split('\0') if name]


def scan_tree(root: Path, markers: tuple[re.Pattern[str], ...] = ()) -> list[str]:
    """``path:line: kind: match`` for every hit in every tracked text file under *root*."""
    found: list[str] = []
    for path in tracked_files(root):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b'\0' in data:
            continue
        text = data.decode('utf-8', errors='replace')
        rel = path.relative_to(root).as_posix()
        found.extend(f'{rel}:{n}: {kind}: {m}' for n, kind, m in scan_text(text, markers, path=rel))
    return found
