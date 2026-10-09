"""Collect node ids PER TEST FILE, reusing what an earlier commit collected for a file with the same content.

Shipped to the cluster as ``~/ci/bin/lab_ci_collect.py`` and run there with the tree's venv, in the
tree. STANDARD LIBRARY ONLY: it must run under whatever ``lab_commons`` the tested project pins.

A file's ids are cached under ``<cache>/<global key>/<blob id>.json``; the GLOBAL key hashes the caller's
seed (the build keys, the marker expression), ``pyproject.toml`` and every ``conftest.py`` -- what can
change the ids of a file whose own content did not change. Only files without a cached entry are
collected, in ONE pytest call; a file that failed to collect is reported and never cached. The output is
``pytest --collect-only -q``'s own shape (ids, then ``ERROR <file>`` lines), written to ``--out`` -- the
caller ``cat``s it -- so the reader is unchanged.

Test files are the tracked ``test_*.py`` / ``*_test.py`` under the given paths (all when none given).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

__all__ = ['main']

_GLOBAL = ('pyproject.toml',)


def _tracked() -> dict[str, str]:
    git = shutil.which('git') or 'git'
    out = subprocess.run([git, 'ls-files', '-s', '-z'], capture_output=True, check=True).stdout.decode('utf-8')
    files: dict[str, str] = {}
    for entry in filter(None, out.split('\0')):
        meta, _, path = entry.partition('\t')
        files[path] = meta.split()[1]
    return files


def _is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith('.py') and (name.startswith('test_') or name.endswith('_test.py'))


def _under(path: str, roots: list[str]) -> bool:
    return not roots or any(path == r or path.startswith(r.rstrip('/') + '/') for r in roots)


def _collect(stale: list[str], marker: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """One ``pytest --collect-only`` over *stale*: ids per file (every stale file present) and ERROR lines."""
    argv = [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider', '--color=no']
    argv += ['-rE', '--continue-on-collection-errors', *marker, *stale]
    env = {k: v for k, v in os.environ.items() if k != 'PYTEST_ADDOPTS'}  # the ERROR summary is the protocol
    text = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', check=False, env=env).stdout
    fresh: dict[str, list[str]] = {path: [] for path in stale}
    # Both shapes pytest prints for a file that failed to import: the summary line and the section header.
    named = [m.group(1) for m in re.finditer(r'(?m)^(?:ERROR |_+ ERROR collecting )(\S+)', text)]
    errors = [f'ERROR {path}' for path in dict.fromkeys(named)]
    for line in text.splitlines():
        if '::' in line and not line.startswith((' ', 'ERROR ')):
            fresh.setdefault(line.strip().split('::')[0], []).append(line.strip())
    return fresh, errors


def main(argv: list[str]) -> int:
    """``--cache DIR --key SEED --out FILE [-m EXPR] -- [paths...]``: the ids, then ERROR lines, into FILE."""
    split = argv.index('--')
    opts, roots = argv[:split], [r.removeprefix('./') for r in argv[split + 1 :] if r not in {'.', './'}]
    cache, seed = Path(opts[opts.index('--cache') + 1]), opts[opts.index('--key') + 1]
    marker = ['-m', opts[opts.index('-m') + 1]] if '-m' in opts else []
    files = _tracked()
    shared = sorted(f'{p} {o}' for p, o in files.items() if p in _GLOBAL or PurePosixPath(p).name == 'conftest.py')
    key = hashlib.sha256('\n'.join([seed, *marker, *shared]).encode('utf-8')).hexdigest()[:24]
    store = cache / key
    tests = [p for p in files if _is_test(p) and _under(p, roots)]
    found: dict[str, list[str]] = {}
    stale = []
    for path in tests:
        entry = store / f'{files[path]}.json'
        if entry.is_file():
            found[path] = json.loads(entry.read_text(encoding='utf-8'))
        else:
            stale.append(path)
    errors: list[str] = []
    if stale:
        fresh, errors = _collect(stale, marker)
        failed = {line[len('ERROR ') :].split(' - ', 1)[0].strip() for line in errors}
        store.mkdir(parents=True, exist_ok=True)
        for path, ids in fresh.items():
            found[path] = ids
            if path not in failed and path in files:
                (store / f'{files[path]}.json').write_text(json.dumps(ids), encoding='utf-8')
    lines = [node for path in tests for node in found.get(path, ())] + errors
    Path(opts[opts.index('--out') + 1]).write_text(''.join(f'{line}\n' for line in lines), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
