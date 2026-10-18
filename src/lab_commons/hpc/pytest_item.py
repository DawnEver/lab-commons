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
import tomllib
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, Final, Protocol

__all__ = [
    'DECIDED',
    'IDS_ENV',
    'ITEM_ENV',
    'MANY_FILE_HEADER',
    'NOT_APPLICABLE_LINE',
    'NOT_APPLICABLE_MARKER',
    'STREAM_ENV',
    'badness',
    'canonical',
    'declared_resources',
    'entry_of',
    'item_argv',
    'judged',
    'pytest_collection_modifyitems',
    'pytest_configure',
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

#: The statuses that are a conclusion about the test: :class:`lab_commons.outcomes.Status`'s DECIDED family,
#: restated by token because this file imports nothing (``tests/test_hpc_pytest_mapping.py`` pins the two equal).
DECIDED: Final = frozenset({'ok', 'rejected', 'not_applicable'})

#: The marker DECLARING that a test may not apply here: a skip of a test carrying it is ``not_applicable``.
NOT_APPLICABLE_MARKER: Final = 'not_applicable'

#: Its registration line (``markers`` ini value), so a run under --strict-markers knows it.
NOT_APPLICABLE_LINE: Final = f'{NOT_APPLICABLE_MARKER}(reason): a skip of this test is NOT_APPLICABLE'

#: The resource markers the tested repo declares (``[tool.lab_commons.tests] resources``,
#: :mod:`lab_commons.hpc.needsets`): a skip of a test carrying one is ``unavailable``. Bound at configure.
_RESOURCES: set[str] = set()

#: The longest reason kept: the crash line, not the traceback -- a 3000-red run is triaged from the record,
#: and a skip or xfail without its reason is an unexplained hole (measured 2026-10-10: 1127 empty skip reasons).
_WHY_CHARS = 300

#: The crash text pytest-timeout writes; the strict-xpass prefix pytest writes.
_TIMEOUT: Final = 'Timeout >'
_STRICT_XPASS: Final = '[XPASS(strict)]'


class _Report(Protocol):
    """What the hook reads of pytest's ``TestReport`` -- pytest itself is not imported here."""

    nodeid: str
    when: str
    outcome: str
    duration: float
    longrepr: object
    #: The test's keywords: its markers' names among them.
    keywords: Mapping[str, object]


def declared_resources(pyproject: Path) -> frozenset[str]:
    """The resource markers *pyproject* declares; none when it is absent or declares none."""
    if not pyproject.is_file():
        return frozenset()
    table = tomllib.loads(pyproject.read_text(encoding='utf-8')).get('tool', {}).get('lab_commons', {})
    return frozenset(table.get('tests', {}).get('resources', []))


def _cell(status: str, kind: str = '', text: str = '', expected: str = '', why: str = '') -> dict[str, Any]:
    """A plain test cell (:class:`lab_commons.hpc.testcells.TestCell`'s plain form), spelled by token."""
    plain: dict[str, Any] = {'status': status}
    if kind:
        plain['reason'] = {'kind': kind, 'text': text[:_WHY_CHARS]}
    if expected:
        plain['expectation'] = {'status': expected, 'why': (why or 'xfail')[:_WHY_CHARS]}
    return plain


def judged(report: _Report, resources: Collection[str]) -> dict[str, Any] | None:
    """THE PYTEST MAPPING: one phase report's plain test cell; ``None`` when the phase decides nothing.

    passed call -> ``ok``; failed call -> ``rejected``/assertion; failed setup or teardown -> ``error``; a
    ``Timeout >`` failure (pytest-timeout) -> ``timeout``; xfail / xpass -> ``rejected`` / ``ok`` under an
    expectation of ``rejected``. A SKIP is mapped by the MARKER that declares it: a declared resource
    marker (*resources*) -> ``unavailable``, ``not_applicable`` -> ``not_applicable``; a skip with
    neither is REFUSED by name -- ``rejected``/xfail_not_skip (XFAIL-NOT-SKIP: a known failure carries
    its residual, a skip carries nothing). A passing setup or teardown says nothing.
    """
    if report.outcome == 'skipped':
        return _skipped(report, resources)
    if report.outcome == 'failed':
        return _failed(report)
    if report.when != 'call':
        return None
    if hasattr(report, 'wasxfail'):  # a non-strict xpass
        return _cell('ok', expected='rejected', why=str(report.wasxfail or ''))
    return _cell('ok')


def _skipped(report: _Report, resources: Collection[str]) -> dict[str, Any]:
    """A skipped phase: an xfail, or a skip mapped by the marker declaring it (:func:`judged`)."""
    if _xfailed(report):
        why = str(getattr(report, 'wasxfail', '') or '')
        return _cell('rejected', 'expected', why or _why(report.longrepr), 'rejected', why)
    reason = _skip_reason(report)
    if any(name in report.keywords for name in resources):
        return _cell('unavailable', 'resource', reason)
    if NOT_APPLICABLE_MARKER in report.keywords:
        return _cell('not_applicable', 'not_applicable', reason)
    refused = f'XFAIL-NOT-SKIP: a skip with no resource marker and no not_applicable(...) is refused: {reason}'
    return _cell('rejected', 'xfail_not_skip', refused)


def _failed(report: _Report) -> dict[str, Any]:
    """A failed phase: a timeout, a strict xpass, a failed call, or a setup/teardown error (:func:`judged`)."""
    text = str(report.longrepr or '')
    if _TIMEOUT in text:
        return _cell('timeout', 'timeout', _why(report.longrepr))
    if text.startswith(_STRICT_XPASS):
        return _cell('ok', expected='rejected', why=text.removeprefix(_STRICT_XPASS).strip())
    if report.when == 'call':
        return _cell('rejected', 'assertion', _why(report.longrepr))
    return _cell('error', report.when, _why(report.longrepr))


def badness(plain: Mapping[str, Any]) -> int:
    """0 for a DECIDED miss, 1 for no answer, 2 for an answer as declared -- the worst phase is the test's."""
    status = str(plain['status'])
    if status not in DECIDED:
        return 1
    expected = (plain.get('expectation') or {}).get('status', 'ok')
    return 2 if status in (expected, 'not_applicable') else 0


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


def _why(longrepr: object) -> str:
    """The crash line of a failed phase: the repr's last non-empty line (pytest ends it ``path:line: Error``)."""
    lines = [line.strip() for line in str(longrepr).splitlines() if line.strip()]
    return lines[-1][:_WHY_CHARS] if lines else ''


def _skip_reason(report: _Report) -> str:
    """A skip's own reason, out of pytest's ``(path, line, reason)``."""
    longrepr = report.longrepr
    return str(longrepr[-1]).removeprefix('Skipped: ') if isinstance(longrepr, tuple) else str(longrepr or '')


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


class _Configured(Protocol):
    """What :func:`pytest_configure` reads of pytest's ``Config``."""

    rootpath: Path

    def addinivalue_line(self, name: str, line: str) -> None:
        """Append *line* to the ini value *name*."""


def pytest_configure(config: _Configured) -> None:
    """Register ``not_applicable`` and bind the tested repo's declared resource markers."""
    config.addinivalue_line('markers', NOT_APPLICABLE_LINE)
    _RESOURCES.update(declared_resources(Path(config.rootpath) / 'pyproject.toml'))


def pytest_runtest_logstart(nodeid: str) -> None:
    """Append a START line before the test's setup, so an exit before its outcome still names the id it hit."""
    _append({'start': nodeid})


def entry_of(report: _Report, resources: Collection[str] = ()) -> dict[str, Any] | None:
    """The stream line one phase report contributes -- its id, seconds and plain test cell; ``None`` for nothing."""
    plain = judged(report, resources)
    if plain is None:
        return None
    return {'id': report.nodeid, 's': round(report.duration, 3), **plain}


def pytest_runtest_logreport(report: _Report) -> None:
    """Append this phase's cell to the stream at once, so a kill a moment later keeps it."""
    if (entry := entry_of(report, _RESOURCES)) is not None:
        _append(entry)


def canonical(node: str, expected: set[str]) -> str:
    """*node* as collected: an xdist ``@<group>`` suffix is dropped when what precedes it is an expected id."""
    if node in expected:
        return node
    head, at, _ = node.rpartition('@')
    return head if at and head in expected else node


#: How bad each word of a RETIRED stream line (``{"id", "outcome": "passed"|..., "why"}``) was -- a round in
#: flight across the change; :func:`lab_commons.hpc.records.fold` upgrades its words into cells.
_RETIRED_BADNESS: Final = {'failed': 0, 'error': 1}


def _line_badness(line: Mapping[str, Any]) -> int:
    if 'status' in line:
        return badness(line)
    return _RETIRED_BADNESS.get(str(line['outcome']), 2)


def read_stream(text: str, node_ids: list[str]) -> dict[str, Any]:
    """Fold one item's stream into ``{cells, seconds, done, started}``.

    ``cells`` holds only the ids the stream reports, each its worst phase's line (:func:`badness`; the first
    of equals): a plain test cell, or -- FORMATS-UPGRADE-ON-READ, a round in flight across the change -- a
    retired ``{"outcome", "why"}`` line. ``seconds`` sums their phases; ``done`` is the closing record, or
    ``None`` when the item never closed -- then every id it did not report is still owed a run. ``started`` is
    the last id the plugin STARTED -- the one in flight if the process exited before its cell.
    """
    expected = set(node_ids)
    cells: dict[str, dict[str, Any]] = {}
    seconds: dict[str, float] = {}
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
        plain = {k: v for k, v in line.items() if k not in ('id', 's', 'item')}
        if node not in cells or _line_badness(plain) < _line_badness(cells[node]):
            cells[node] = plain
        seconds[node] = round(seconds.get(node, 0.0) + float(line.get('s', 0.0)), 3)
    return {'cells': cells, 'seconds': seconds, 'done': done, 'started': started}


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
