"""One remote-verdict item on a compute node: run a group of pytest node ids, return each one's outcome.

STANDALONE AND STDLIB-ONLY ON PURPOSE. :mod:`lab_commons.hpc.verdict` ships this file's TEXT to the
cluster (``~/ci/bin/lab_ci_pytest_item.py``) and the array task imports it from there inside the tested
tree's own venv -- so the outcome parser that runs is the one this checkout wrote, whatever version of
``lab_commons`` the tested project happens to pin. It imports nothing from ``lab_commons``.

THE JUNIT FILE IS THE RECORD, NOT THE EXIT CODE. ``pytest`` writes ``--junitxml`` per item; an id that
ran is read back from it by the address pytest itself mangles into ``classname``/``name``, and an id the
file does not mention (the interpreter died, the collection changed) is ``missing`` -- never assumed to
have passed.
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

__all__ = ['junit_key', 'parse_junit', 'run']

#: Worst first: a test with a failing call and an erroring teardown is ``failed``.
_RANK = ('failed', 'error', 'xfailed', 'skipped', 'passed')


def junit_key(node_id: str) -> tuple[str, str]:
    """The ``(classname, name)`` pytest's junit writer gives *node_id* (``_pytest.junitxml.mangle_test_address``)."""
    path, bracket, params = node_id.partition('[')
    names = path.split('::')
    names[0] = re.sub(r'\.py$', '', names[0].replace('/', '.'))
    names[-1] += bracket + params
    return '.'.join(names[:-1]), names[-1]


def _outcome(case: ET.Element) -> str:
    found = []
    for child in case:
        if child.tag == 'failure':
            found.append('failed')
        elif child.tag == 'error':
            found.append('error')
        elif child.tag == 'skipped':
            found.append('xfailed' if child.get('type') == 'pytest.xfail' else 'skipped')
    return min(found, key=_RANK.index) if found else 'passed'


def parse_junit(text: str, node_ids: list[str]) -> dict[str, str]:
    """Each of *node_ids*' outcome in a junit document; an id it does not mention is ``missing``."""
    seen: dict[tuple[str, str], str] = {}
    for case in ET.fromstring(text).iter('testcase'):  # noqa: S314 -- our own pytest's output, not untrusted input
        key = (case.get('classname', ''), case.get('name', ''))
        outcome = _outcome(case)
        seen[key] = min(seen.get(key, outcome), outcome, key=_RANK.index)
    return {node: seen.get(junit_key(node), 'missing') for node in node_ids}


def run(item: dict[str, Any]) -> dict[str, Any]:
    """Run ``item['ids']`` in the current directory with this interpreter; outcomes per id, exit code, tail."""
    ids = list(item['ids'])
    junit = Path(item['junit'])
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)
    argv = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', f'--junitxml={junit}', *ids]
    done = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    text = junit.read_text(encoding='utf-8') if junit.is_file() else '<testsuites/>'
    tail = (done.stdout + done.stderr).strip().splitlines()[-20:]
    return {'rc': done.returncode, 'outcomes': parse_junit(text, ids), 'tail': '\n'.join(tail)}
