"""RETIRED SPELLINGS of the test-run surface, each with its replacement -- the one place they may appear.

The noun that named three things (forge plan P1, user ruling 2026-10-09) was split into a RUN (one
execution of a test set on one platform at one sha; its record is ``run-<sha>-<platform>-<req>.json.gz``),
a PART (a platform's result for a sha, composed of runs) and an ADMISSION (computed, never stored).
No alias survives: a command line still using a retired word is REFUSED BY NAME with the remedy
(:func:`refusal`), and ``tests/test_hpc_retired.py`` reds on any retired spelling anywhere else in
the tracked tree, prose included.
"""

from __future__ import annotations

from typing import Final

__all__ = ['RETIRED', 'refusal']

#: Retired spelling -> its replacement. Keys are matched as plain substrings of tracked text.
RETIRED: Final[dict[str, str]] = {
    'lab_commons.hpc verdict': 'python -m lab_commons.hpc run submit|gather (a run, not a verdict)',
    'lab_commons.hpc.verdict': 'lab_commons.hpc.run',
    'submit_verdict': 'lab_commons.hpc.run.submit_run',
    'gather_verdict': 'lab_commons.hpc.run.gather_run',
    'VerdictSpec': 'lab_commons.hpc.shell.RunSpec',
    'record-linux': 'python -m lab_commons.dev.platformparts record <run-record> (platform and scope come from it)',
    '<job>.run.json': '<job>.submission.json (the last submission of a job file)',
}


def refusal(prog: str, word: str) -> str | None:
    """The refusal for *word* used as a verb of *prog*, naming its replacement; ``None`` when it is not retired."""
    remedy = RETIRED.get(f'{prog} {word}') or RETIRED.get(word)
    return None if remedy is None else f'`{prog} {word}` is retired: use {remedy}'
