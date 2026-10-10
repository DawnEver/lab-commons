"""One remote-run item on a compute node: run a group of pytest node ids, STREAM each one's outcome.

STANDALONE AND STDLIB-ONLY ON PURPOSE. :mod:`lab_commons.hpc.run` ships this file's TEXT to the
cluster (``~/ci/bin/lab_ci_pytest_item.py``) and the array task imports it from there inside the tested
tree's own venv -- so the code that runs is the one this checkout wrote, whatever version of
``lab_commons`` the tested project happens to pin. It imports nothing from ``lab_commons``.

THE SAME FILE IS THE PYTEST PLUGIN. :func:`run` starts ``pytest -p lab_ci_pytest_item``; the hook below
appends one JSON line per test phase to the item's stream file the moment pytest reports it, and
:func:`run` appends a closing ``done`` line (exit code, wall seconds, peak RSS). A shard Slurm kills for
time or memory therefore leaves every test it FINISHED on disk; an item without its ``done`` line is
UNFINISHED and is re-run, never assumed. Measured on a real cluster (a downstream run, 2026-10-09):
23 of 99 shards were killed at the wall limit, and the end-of-shard results file they never wrote took
13751 ids down with them as ``lost`` -- finished ones included.

THE ID IS PYTEST'S OWN ``nodeid``, NOT A JUNIT RE-MANGLING. The junit file this replaced was keyed on
``(classname, name)``, and pytest-xdist's ``--dist loadgroup`` appends ``@<group>`` to a grouped test's id
inside its workers -- measured on the same run: ``name="test_unknown_attribute_raises_attribute_error
@group_0"`` matched no collected id, and 8786 tests that RAN were recorded ``missing``. The run
asks for ``-n 0`` when xdist is installed (an item has one CPU; an xdist worker is a second interpreter
importing the whole tree again, per file, for nothing), and :func:`canonical` strips a suffix that still
arrives.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    'IDS_ENV',
    'STREAM_ENV',
    'canonical',
    'outcome_of',
    'pytest_collection_modifyitems',
    'pytest_runtest_logreport',
    'read_stream',
    'run',
]

#: The environment variable naming the stream file the plugin appends to.
STREAM_ENV = 'LAB_CI_STREAM'

#: The environment variable naming a JSON list of the node ids to keep -- selection is by id, not by argv.
IDS_ENV = 'LAB_CI_IDS'

#: Worst first: a test with a failing call and an erroring teardown is ``failed``.
_RANK = ('failed', 'error', 'xfailed', 'skipped', 'passed')


class _Report(Protocol):
    """What the hook reads of pytest's ``TestReport`` -- pytest itself is not imported here."""

    nodeid: str
    when: str
    outcome: str
    duration: float
    longrepr: object


def outcome_of(when: str, outcome: str, *, xfail: bool) -> str | None:
    """One phase report's contribution to its test's outcome; ``None`` when the phase decides nothing.

    A passing setup or teardown says nothing -- only a passing CALL makes a test ``passed``.
    """
    if outcome == 'skipped':
        return 'xfailed' if xfail else 'skipped'
    if outcome == 'failed':
        return 'failed' if when == 'call' else 'error'
    return 'passed' if when == 'call' else None


class _Item(Protocol):
    nodeid: str


class _Config(Protocol):
    hook: Any


def pytest_collection_modifyitems(config: _Config, items: list[_Item]) -> None:
    """Keep only the item's ids -- the FILES go on the command line, this hook picks the ids out of them.

    The ids are NOT passed as arguments: pytest cannot parse every id it prints
    back (a ``::`` or an escaped non-ASCII character inside a parameter), and one it cannot parse aborts the
    whole file with ``no tests ran``.
    """
    source = os.environ.get(IDS_ENV)
    if not source:
        return
    keep = set(json.loads(Path(source).read_text(encoding='utf-8')))
    chosen = [item for item in items if item.nodeid in keep]
    dropped = [item for item in items if item.nodeid not in keep]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = chosen


def _xfailed(report: _Report) -> bool:
    """A skip carries pytest's ``(path, line, reason)`` tuple; an xfail carries the failure it expected."""
    return report.outcome == 'skipped' and not isinstance(report.longrepr, tuple)


#: The outcomes whose reason the stream keeps -- a 3000-red run is triaged from the record, not a re-run.
_RED = ('failed', 'error')

#: The longest reason kept: the crash line, not the traceback.
_WHY_CHARS = 300


def _why(longrepr: object) -> str:
    """The crash line of a failed phase: the repr's last non-empty line (pytest ends it ``path:line: Error``)."""
    lines = [line.strip() for line in str(longrepr).splitlines() if line.strip()]
    return lines[-1][:_WHY_CHARS] if lines else ''


def pytest_runtest_logreport(report: _Report) -> None:
    """Append this phase's outcome to the stream at once, so a kill a moment later keeps it."""
    target = os.environ.get(STREAM_ENV)
    outcome = outcome_of(report.when, report.outcome, xfail=_xfailed(report))
    if not target or outcome is None:
        return
    entry: dict[str, Any] = {'id': report.nodeid, 'outcome': outcome, 's': round(report.duration, 3)}
    if outcome in _RED:
        entry['why'] = _why(report.longrepr)
    line = json.dumps(entry)
    # builtin open, not pathlib: a test that fakes os.name makes Path() spell a Windows path on the cluster
    with open(target, 'a', encoding='utf-8') as stream:  # noqa: PTH123
        stream.write(line + '\n')


def canonical(node: str, expected: set[str]) -> str:
    """*node* as collected: an xdist ``@<group>`` suffix is dropped when what precedes it is an expected id."""
    if node in expected:
        return node
    head, at, _ = node.rpartition('@')
    return head if at and head in expected else node


def read_stream(text: str, node_ids: list[str]) -> dict[str, Any]:
    """Fold one item's stream into ``{outcomes, seconds, whys, done}``.

    ``outcomes`` holds only the ids the stream reports (the worst phase wins); ``seconds`` sums their
    phases; ``whys`` holds the reason each red id's worst phase carried; ``done`` is the closing record,
    or ``None`` when the item never closed -- then every id it did not report is still owed a run.
    """
    expected = set(node_ids)
    outcomes: dict[str, str] = {}
    seconds: dict[str, float] = {}
    whys: dict[str, str] = {}
    done = None
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue  # the half-written last line of a killed task
        if 'done' in line:
            done = line
            continue
        node = canonical(line['id'], expected)
        if node not in expected:
            continue
        outcome = line['outcome']
        if 'why' in line and (node not in outcomes or _RANK.index(outcome) < _RANK.index(outcomes[node])):
            whys[node] = line['why']
        outcomes[node] = min(outcomes.get(node, outcome), outcome, key=_RANK.index)
        seconds[node] = round(seconds.get(node, 0.0) + float(line.get('s', 0.0)), 3)
    return {'outcomes': outcomes, 'seconds': seconds, 'whys': whys, 'done': done}


def _wait(proc: subprocess.Popen[bytes]) -> tuple[int, float | None]:
    """Exit code and the child's peak RSS in MB -- ``wait4`` where the OS has it, else no peak."""
    if sys.platform == 'win32':
        return proc.wait(), None
    _, status, usage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return proc.returncode, round(usage.ru_maxrss / 1024, 1)  # Linux reports KiB


def run(item: dict[str, Any]) -> dict[str, Any]:
    """Run ``item['ids']`` streaming to ``item['stream']``; the folded stream plus the output's tail."""
    ids = list(item['ids'])
    stream = Path(item['stream'])
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.unlink(missing_ok=True)
    serial = ['-n', '0'] if importlib.util.find_spec('xdist') else []
    chosen = stream.with_suffix('.ids.json')
    chosen.write_text(json.dumps(ids), encoding='utf-8')
    files = list(dict.fromkeys(node.split('::', maxsplit=1)[0] for node in ids))
    argv = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-p', Path(__file__).stem, '-q', *serial, *files]
    env = {**os.environ, STREAM_ENV: str(stream.resolve()), IDS_ENV: str(chosen.resolve())}
    start = time.monotonic()
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, env=env)
        rc, peak = _wait(proc)
        out.seek(0)
        tail = out.read().decode('utf-8', 'replace').strip().splitlines()[-20:]
    done = {'done': rc, 'wall': round(time.monotonic() - start, 3), 'peak_mb': peak}
    with stream.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(done) + '\n')
    return {**read_stream(stream.read_text(encoding='utf-8'), ids), 'tail': '\n'.join(tail)}
