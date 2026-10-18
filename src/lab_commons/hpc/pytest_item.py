"""One remote-run item on a compute node: run a group of pytest node ids, STREAM each one's outcome.

STANDALONE AND STDLIB-ONLY ON PURPOSE. :mod:`lab_commons.hpc.run` ships this file's TEXT to the
cluster (``~/ci/bin/lab_ci_pytest_item.py``) and the array task imports it from there inside the tested
tree's own venv -- so the code that runs is the one this checkout wrote, whatever version of
``lab_commons`` the tested project happens to pin. It imports nothing from ``lab_commons``.

THE SAME FILE IS THE PYTEST PLUGIN. :func:`run` starts ``pytest -p lab_ci_pytest_item``; the hook below
appends one JSON line per test phase to the ROUND's stream file the moment pytest reports it, and
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

ONE FILE PER ROUND, NOT TWO PER ITEM (``runs/*/streams`` measured 600-1000 files per sha, 2026-10-17; GPFS
counts files against a quota). Every item of a round appends to ``.lab-ci/<tag>.jsonl``, each line tagged
``"item": <index>`` and written by ONE ``O_APPEND`` write -- whole lines from many nodes interleave, they do
not tear. An attempt opens with ``{"item": i, "begin": ...}``, so a restarted item reads its last attempt
only (:func:`split_streams`); its id list goes to node-local scratch. FORMATS-UPGRADE-ON-READ: a round
submitted before the change (``.lab-ci/<tag>/<i>.jsonl``, one file per item) is upgraded one way -- its
manifest by :func:`run` (the new runner appends to the round file), what it already wrote by
:func:`tagged` (``@@@ <i>.jsonl`` sections) -- and nothing writes that shape any more.
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
from typing import Any, Final, Protocol

__all__ = [
    'IDS_ENV',
    'ITEM_ENV',
    'MANY_FILE_HEADER',
    'STREAM_ENV',
    'canonical',
    'entry_of',
    'item_argv',
    'outcome_of',
    'pytest_collection_modifyitems',
    'pytest_runtest_logreport',
    'pytest_runtest_logstart',
    'read_stream',
    'run',
    'split_streams',
    'tagged',
]

#: The environment variable naming the round's stream file the plugin appends to.
STREAM_ENV = 'LAB_CI_STREAM'

#: The environment variable naming the item's index in its round -- every line it appends carries it.
ITEM_ENV = 'LAB_CI_ITEM'

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
    #: Set by pytest on an xfailed report only -- read only once :func:`_xfailed` said so.
    wasxfail: str


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


#: The outcomes whose reason the stream keeps -- a 3000-red run is triaged from the record, not a re-run, and
#: a skip or xfail without its reason is an unexplained hole (measured 2026-10-10: 1127 empty skip reasons).
_REASONED = ('failed', 'error', 'skipped', 'xfailed')

#: The longest reason kept: the crash line, not the traceback.
_WHY_CHARS = 300


def _why(longrepr: object) -> str:
    """The crash line of a failed phase: the repr's last non-empty line (pytest ends it ``path:line: Error``)."""
    lines = [line.strip() for line in str(longrepr).splitlines() if line.strip()]
    return lines[-1][:_WHY_CHARS] if lines else ''


def _reason(report: _Report, outcome: str) -> str:
    """Why a phase ended as it did: a skip's own reason, an xfail's expectation, else the crash line."""
    if outcome == 'skipped' and isinstance(report.longrepr, tuple):
        return str(report.longrepr[-1]).removeprefix('Skipped: ')[:_WHY_CHARS]
    if outcome == 'xfailed':
        return str(report.wasxfail or _why(report.longrepr))[:_WHY_CHARS]
    return _why(report.longrepr)


#: The stream this process appends to, BOUND ONCE when the plugin loads -- the RUN's, never a test's.
#: Read per append until 2026-10-12, when two tests that set ``LAB_CI_STREAM`` themselves sent their own
#: outcomes to their own stream and the cluster run recorded them ``missing``.
_TARGET: Final = os.environ.get(STREAM_ENV)
_ITEM: Final = os.environ.get(ITEM_ENV)


def _write(target: str, item: int, entry: dict[str, Any]) -> None:
    """Append *entry* tagged with *item* as ONE write: lines of a round's items interleave whole."""
    line = (json.dumps({'item': item, **entry}) + '\n').encode('utf-8')
    # os.open, not pathlib: a test that fakes os.name makes Path() spell a Windows path on the cluster
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _append(entry: dict[str, Any]) -> None:
    if _TARGET and _ITEM is not None:
        _write(_TARGET, int(_ITEM), entry)


def pytest_runtest_logstart(nodeid: str) -> None:
    """Append a START line before the test's setup, so an exit before its outcome still names the id it hit."""
    _append({'start': nodeid})


def entry_of(report: _Report) -> dict[str, Any] | None:
    """The stream line one phase report contributes; ``None`` when the phase decides nothing."""
    outcome = outcome_of(report.when, report.outcome, xfail=_xfailed(report))
    if outcome is None:
        return None
    entry: dict[str, Any] = {'id': report.nodeid, 'outcome': outcome, 's': round(report.duration, 3)}
    if outcome in _REASONED:
        entry['why'] = _reason(report, outcome)
    return entry


def pytest_runtest_logreport(report: _Report) -> None:
    """Append this phase's outcome to the stream at once, so a kill a moment later keeps it."""
    if (entry := entry_of(report)) is not None:
        _append(entry)


def canonical(node: str, expected: set[str]) -> str:
    """*node* as collected: an xdist ``@<group>`` suffix is dropped when what precedes it is an expected id."""
    if node in expected:
        return node
    head, at, _ = node.rpartition('@')
    return head if at and head in expected else node


def read_stream(text: str, node_ids: list[str]) -> dict[str, Any]:
    """Fold one item's stream into ``{outcomes, seconds, whys, done, started}``.

    ``outcomes`` holds only the ids the stream reports (the worst phase wins); ``seconds`` sums their
    phases; ``whys`` holds the reason each red, skipped or xfailed id's worst phase carried; ``done`` is the
    closing record, or ``None`` when the item never closed -- then every id it did not report is still owed a run.
    ``started`` is the last id the plugin STARTED -- the one in flight if the process exited before its outcome.
    """
    expected = set(node_ids)
    outcomes: dict[str, str] = {}
    seconds: dict[str, float] = {}
    whys: dict[str, str] = {}
    done = None
    started = None
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue  # the half-written last line of a killed task
        if 'done' in line:
            done = line
            continue
        if 'start' in line:
            started = canonical(line['start'], expected)
            continue
        node = canonical(line['id'], expected)
        if node not in expected:
            continue
        outcome = line['outcome']
        if 'why' in line and (node not in outcomes or _RANK.index(outcome) < _RANK.index(outcomes[node])):
            whys[node] = line['why']
        outcomes[node] = min(outcomes.get(node, outcome), outcome, key=_RANK.index)
        seconds[node] = round(seconds.get(node, 0.0) + float(line.get('s', 0.0)), 3)
    return {'outcomes': outcomes, 'seconds': seconds, 'whys': whys, 'done': done, 'started': started}


#: The header of one item's section in the retired many-file listing (one ``<i>.jsonl`` per item).
MANY_FILE_HEADER: Final = '@@@ '


def tagged(text: str) -> list[dict[str, Any]]:
    """*text*'s stream lines as tagged entries, the retired ``@@@ <i>.jsonl`` sections upgraded on the way.

    A line that is no JSON object -- the half-written last line of a killed task -- is dropped.
    """
    entries: list[dict[str, Any]] = []
    section: int | None = None
    for raw in text.splitlines():
        if raw.startswith(MANY_FILE_HEADER) and raw.endswith('.jsonl'):
            section = int(raw[len(MANY_FILE_HEADER) : -len('.jsonl')])
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict):
            continue
        if 'item' not in line and 'round' not in line:
            if section is None:
                continue
            line = {'item': section, **line}
        entries.append(line)
    return entries


def split_streams(text: str, tag: str) -> dict[int, str]:
    """Each item's stream of round *tag* out of one round's *text*: its last attempt's lines.

    A ``{"round": ...}`` header (a kept file, :func:`lab_commons.hpc.records.kept_text`) naming another round
    makes what follows it no stream of this one.
    """
    streams: dict[int, list[str]] = {}
    ours = True
    for line in tagged(text):
        if 'round' in line:
            ours = line['round'] == tag
        elif ours and 'begin' in line:
            streams[int(line['item'])] = []
        elif ours:
            streams.setdefault(int(line['item']), []).append(json.dumps(line))
    return {index: '\n'.join(lines) for index, lines in streams.items()}


def _wait(proc: subprocess.Popen[bytes]) -> tuple[int, float | None]:
    """Exit code and the child's peak RSS in MB -- ``wait4`` where the OS has it, else no peak."""
    if sys.platform == 'win32':
        return proc.wait(), None
    _, status, usage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return proc.returncode, round(usage.ru_maxrss / 1024, 1)  # Linux reports KiB


def item_argv(item: dict[str, Any], files: list[str]) -> list[str]:
    """The pytest command for *item*: this file as the plugin, serial, and ``--timeout`` when its scope declares one."""
    serial = ['-n', '0'] if importlib.util.find_spec('xdist') else []
    ceiling = ['--timeout', str(item['timeout_s'])] if item.get('timeout_s') else []
    plugin = Path(__file__).stem
    return [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-p', plugin, '-q', *serial, *ceiling, *files]


def _upgraded(item: dict[str, Any]) -> tuple[Path, int]:
    """The round file and index of *item*; a manifest of the retired ``<tag>/<i>.jsonl`` shape, upgraded."""
    stream = Path(item['stream'])
    if 'item' in item:
        return stream, int(item['item'])
    return stream.parent.with_suffix('.jsonl'), int(stream.stem)


def run(item: dict[str, Any]) -> dict[str, Any]:
    """Run ``item['ids']`` streaming to its round's file; the folded stream plus the output's tail."""
    ids = list(item['ids'])
    stream, index = _upgraded(item)
    stream.parent.mkdir(parents=True, exist_ok=True)
    target = str(stream.resolve())
    _write(target, index, {'begin': time.time()})
    files = list(dict.fromkeys(node.split('::', maxsplit=1)[0] for node in ids))
    argv = item_argv(item, files)
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as scratch, tempfile.TemporaryFile() as out:  # node-local, never GPFS
        chosen = Path(scratch) / 'ids.json'
        chosen.write_text(json.dumps(ids), encoding='utf-8')
        env = {**os.environ, STREAM_ENV: target, ITEM_ENV: str(index), IDS_ENV: str(chosen)}
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, env=env)
        rc, peak = _wait(proc)
        out.seek(0)
        tail = out.read().decode('utf-8', 'replace').strip().splitlines()[-20:]
    _write(target, index, {'done': rc, 'wall': round(time.monotonic() - start, 3), 'peak_mb': peak})
    mine = split_streams(stream.read_text(encoding='utf-8'), '').get(index, '')
    return {**read_stream(mine, ids), 'tail': '\n'.join(tail)}
