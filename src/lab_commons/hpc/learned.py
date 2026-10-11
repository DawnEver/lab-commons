"""What the last run of a repository MEASURED, carried to its next submit -- one file per repository.

``<config dir>/hpc-learned/<key>.json`` (beside :mod:`lab_commons.hpc.outstanding`), keyed by the
remote URL: writing a run's record replaces it with the record's measurements
(:class:`~lab_commons.hpc.measured.Measured`), and ``run submit`` plans from it. Machine-local, so it
serves every grant the machine holds.

WHY IT IS NOT A FLAG. Measured 2026-10-11: ``run submit --history <record>`` was optional and no caller
passed it, so every downstream run planned round 0 blind -- 4441 of 4441 files priced at ``[cost]`` and
1 GB, 26 of 93 shards OUT_OF_MEMORY, two retry rounds that took 3.0 of the run's 4.2 hours.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lab_commons.config import config_path
from lab_commons.hpc.measured import Measured

__all__ = ['directory', 'learn', 'learned']


def directory() -> Path:
    """``hpc-learned/`` beside the machine config (:func:`lab_commons.config.config_path`)."""
    return config_path().parent / 'hpc-learned'


def _path(repo: str) -> Path:
    return directory() / f'{hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]}.json'


def learn(record: dict[str, Any]) -> Path:
    """Keep *record*'s measurements as its repository's latest; returns the file written."""
    target = _path(record['repo'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'repo': record['repo'], **Measured.of(record).record()}), encoding='utf-8')
    return target


def learned(repo: str) -> dict[str, Any]:
    """The measurements last learned for *repo*; nothing learned yet is an empty record."""
    path = _path(repo)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
