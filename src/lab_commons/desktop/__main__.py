"""``python -m lab_commons.desktop windows|capture|controls|press|set-value|close [--pid N]... [--title S] [--json]``.

Every verb selects windows by ``--pid`` (repeatable), ``--title`` (a case-blind substring) and ``--handle``.
Reading verbs act on every selected window; ``press``, ``set-value`` and ``close`` need exactly ONE, and
``--pid`` is then also the set of pids they may touch (:func:`lab_commons.desktop.press`). ``--json``
prints one JSON array; a refusal prints ``desktop: <reason> -- <why>`` on stderr and exits 1.
``windows`` adds each window's process CPU seconds (:func:`lab_commons.proccpu.cpu_seconds`), so two
listings a moment apart tell a busy window from a hung one.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from lab_commons.desktop._core import DesktopError, Window
from lab_commons.desktop._door import capture, close, controls, press, set_value, windows
from lab_commons.log import emit
from lab_commons.proccpu import cpu_seconds

_ACTS = ('press', 'set-value', 'close')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.desktop')
    verbs = parser.add_subparsers(dest='verb', required=True)
    helps = {
        'windows': 'list top-level windows with pid, title, rect, enabled, responding, cpu_s',
        'capture': 'write each window as a PNG into --out',
        'controls': "read each window's accessibility tree (role, name, value, enabled, actions)",
        'press': 'press the ONE control labelled exactly --label (or with --id), once',
        'set-value': 'set the ONE control labelled exactly --label (or with --id) to --value',
        'close': 'ask the window to close through its own close',
    }
    for verb, text in helps.items():
        sub = verbs.add_parser(verb, help=text)
        sub.add_argument(
            '--pid', type=int, action='append', default=[], help='a pid (repeatable); acts may touch only these'
        )
        sub.add_argument('--title', default=None, help='a case-blind substring of the title')
        sub.add_argument('--handle', type=lambda t: int(t, 0), default=None, help='one window handle (0x.. or decimal)')
        sub.add_argument('--json', action='store_true', help='one JSON array')
        if verb == 'windows':
            sub.add_argument('--hidden', action='store_true', help='include windows that are not visible')
        if verb == 'capture':
            sub.add_argument('--out', type=Path, required=True, help='the directory the PNGs go to')
            sub.add_argument('--timeout-s', type=float, default=5.0, help='seconds before a window is busy')
        if verb in {'press', 'set-value'}:
            target = sub.add_mutually_exclusive_group(required=True)
            target.add_argument('--label', help="the control's exact label")
            target.add_argument('--id', help="the control's id, as `controls` lists it")
        if verb == 'set-value':
            sub.add_argument('--value', required=True, help='the text to set')
    return parser


def _row(window: Window) -> dict[str, object]:
    return {**dataclasses.asdict(window), 'rect': list(window.rect), 'cpu_s': cpu_seconds(window.pid)}


def _print(rows: list[dict[str, object]], *, as_json: bool) -> None:
    if as_json:
        emit(json.dumps(rows))
        return
    for row in rows:
        emit('  '.join(f'{key}={value}' for key, value in row.items()))


def _run(ns: argparse.Namespace) -> list[dict[str, object]]:
    selected = [
        w
        for w in windows(ns.pid or None, ns.title, hidden=getattr(ns, 'hidden', False))
        if ns.handle is None or w.handle == ns.handle
    ]
    if ns.verb == 'windows':
        return [_row(w) for w in selected]
    if ns.verb == 'capture':
        ns.out.mkdir(parents=True, exist_ok=True)
        rows = []
        for w in selected:
            path = ns.out / f'window-{w.pid}-{w.handle:x}.png'
            path.write_bytes(capture(w, timeout_s=ns.timeout_s))
            rows.append({'handle': w.handle, 'pid': w.pid, 'title': w.title, 'png': str(path)})
        return rows
    if ns.verb == 'controls':
        return [
            {'handle': w.handle, 'pid': w.pid, **dataclasses.asdict(c), 'actions': list(c.actions)}
            for w in selected
            for c in controls(w)
        ]
    if not ns.pid:
        msg = f'{ns.verb} needs --pid: the pids it may touch'
        raise _Usage(msg)
    if len(selected) != 1:
        msg = f'{ns.verb} needs exactly one window; --pid/--title/--handle selected {len(selected)}'
        raise _Usage(msg)
    (window,) = selected
    if ns.verb == 'press':
        press(window, ns.label, id=ns.id, owned=ns.pid)
    elif ns.verb == 'set-value':
        set_value(window, ns.label, ns.value, id=ns.id, owned=ns.pid)
    else:
        close(window, owned=ns.pid)
    return [{'handle': window.handle, 'pid': window.pid, 'title': window.title, 'done': ns.verb}]


class _Usage(Exception):
    """A selection the verb cannot act on."""


def main(args: Sequence[str] | None = None) -> int:
    """Parse, run one verb, print its rows; 1 on a refusal (named on stderr), 2 on a usage error."""
    parser = _parser()
    ns = parser.parse_args(args)
    try:
        rows = _run(ns)
    except _Usage as usage:
        parser.error(str(usage))
    except DesktopError as refused:
        emit(f'desktop: {refused.reason} -- {refused}', err=True)
        return 1
    _print(rows, as_json=ns.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
