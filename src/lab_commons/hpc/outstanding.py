"""THE list of this machine's outstanding cluster runs -- one file per run, next to the machine config.

``run submit`` adds ``<config dir>/hpc-runs/<sha>.json`` (sha, repo, grant, job ids, submitted-at);
writing the run's record removes it. There is no other list: ``run status`` and ``run watch`` read
this directory, and the run's own progress (rounds, current job) stays on the cluster
(:mod:`lab_commons.hpc.run`). One file per run, so two submissions never race on one document.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from lab_commons.config import config_path

__all__ = ['add', 'directory', 'outstanding', 'remove']


def directory() -> Path:
    """``hpc-runs/`` beside the machine config (:func:`lab_commons.config.config_path`)."""
    return config_path().parent / 'hpc-runs'


def add(sha: str, *, repo: str, grant: str, job_ids: list[str]) -> Path:
    """Record *sha* as outstanding; returns the entry's path."""
    target = directory() / f'{sha}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {'sha': sha, 'repo': repo, 'grant': grant, 'job_ids': job_ids, 'submitted_at': time.time()}
    target.write_text(json.dumps(entry, indent=2), encoding='utf-8')
    return target


def outstanding() -> list[dict[str, Any]]:
    """Every outstanding run, oldest submission first."""
    entries = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(directory().glob('*.json'))]
    return sorted(entries, key=lambda e: e['submitted_at'])


def remove(sha: str) -> None:
    """Forget *sha* -- its record was written. Absent is fine: a run gathered by hand was never listed."""
    (directory() / f'{sha}.json').unlink(missing_ok=True)
