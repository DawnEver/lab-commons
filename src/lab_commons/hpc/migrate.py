"""``python -m lab_commons.hpc migrate <file>...`` -- bring run states and run records to the current shape ONCE.

FORMATS-MIGRATE-ONCE (D44, 2026-11-17): the readers keep only the current shape and refuse an older one by name
(:class:`~lab_commons.compute.measured.UnmigratedRecord`), so a stored record crosses a format change here, once,
never on every read. Two shapes are migrated:

- a run STATE (``~/ci/runs/<sha>/<part>/state.json``) written before ``item_wall`` existed gets the
  declared :class:`~lab_commons.hpc.builds.ItemWall` defaults, and its ``measured`` every
  :class:`~lab_commons.compute.measured.Measured` key it lacks, empty;
- a run RECORD (``run-<sha>-<part>-<req>.json.gz``) gets every ``Measured`` key it lacks, empty.

What a record lacked it never measured, so empty is the truth, not a guess. A current file is left untouched
(a second run is a no-op). Run it where the files are -- a state on the cluster's login node.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lab_commons.compute.durable import durable_write
from lab_commons.compute.measured import Measured
from lab_commons.hpc.builds import ItemWall

__all__ = ['main', 'migrate_file', 'migrated_measured', 'migrated_state']


def migrated_measured(record: dict[str, Any]) -> dict[str, Any]:
    """*record* with every :class:`Measured` key it lacks added empty."""
    return {**{key: {} for key in Measured().record() if key not in record}, **record}


def migrated_state(state: dict[str, Any]) -> dict[str, Any]:
    """A run *state* with ``item_wall`` (declared defaults) and a current ``measured``."""
    return {
        **state,
        'item_wall': state.get('item_wall', asdict(ItemWall())),
        'measured': migrated_measured(state.get('measured', {})),
    }


def migrate_file(path: Path) -> bool:
    """Migrate the state or record at *path* in place; ``True`` when it changed, ``False`` when already current."""
    record_file = path.name.endswith('.json.gz')
    raw = gzip.decompress(path.read_bytes()) if record_file else path.read_bytes()
    old = json.loads(raw.decode('utf-8'))
    new = migrated_measured(old) if record_file else migrated_state(old)
    if new == old:
        return False
    text = json.dumps(new).encode('utf-8')
    durable_write(path, gzip.compress(text, mtime=0) if record_file else text)
    return True


def main(argv: Sequence[str]) -> int:
    """Migrate each named file; print one line per file saying whether it changed."""
    parser = argparse.ArgumentParser(prog='python -m lab_commons.hpc migrate', description=__doc__.splitlines()[0])
    parser.add_argument('files', nargs='+', type=Path, help='state.json or run-*.json.gz files')
    args = parser.parse_args(argv)
    for path in args.files:
        sys.stdout.write(f'{"migrated" if migrate_file(path) else "current"} {path}\n')
    return 0
