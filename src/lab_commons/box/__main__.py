"""``python -m lab_commons.box run --cpu N --mem-gb G --wall S --queue-s Q --key K --outcome P [...] -- argv``.

Every limit is REQUIRED, exactly as :class:`~lab_commons.box.Demand` has no defaults: a missing one
is an argparse refusal, never a number nobody chose. So is ``--outcome``: the box's decision is the
record written there (:func:`~lab_commons.box.write_ended`) -- the :class:`~lab_commons.box.Outcome`, or the
refusal when nothing ran -- and NEVER the exit code. The exit code is the child's own, and a child may
exit with any number, so a sentinel code would misread a child that happened to choose it. On a refusal
the code is nonzero and means nothing: read the record. Both streams stay the child's.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from lab_commons.box.demand import Capacity, Demand, Refused, write_ended
from lab_commons.box.run import run
from lab_commons.log import emit


def _seat(text: str) -> tuple[str, int]:
    name, sep, count = text.partition('=')
    if not sep or not name or not count.isdigit():
        msg = f'--seat wants name=count, got {text!r}'
        raise argparse.ArgumentTypeError(msg)
    return name, int(count)


def _capacity(text: str) -> Capacity:
    """The :class:`Capacity` the caller sized its demand against, as :func:`~lab_commons.box.run.command` spells it."""
    try:
        return Capacity(**json.loads(text))
    except (ValueError, TypeError) as bad:
        msg = f'--capacity wants the JSON of a Capacity, got {text!r}'
        raise argparse.ArgumentTypeError(msg) from bad


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.box')
    verbs = parser.add_subparsers(dest='verb', required=True)
    verb = verbs.add_parser('run', help='admit, run enforced to the claim, release')
    verb.add_argument('--cpu', type=int, required=True, help='cores claimed (a hard CPU cap)')
    verb.add_argument('--mem-gb', type=float, required=True, help='memory claimed (a hard limit on the tree)')
    verb.add_argument('--wall', type=int, required=True, help='seconds until the tree is killed')
    verb.add_argument('--queue-s', type=float, required=True, help='seconds to wait for room, then refuse')
    verb.add_argument('--seat', type=_seat, action='append', default=[], help='a tool seat, name=count')
    verb.add_argument('--key', required=True, help="the caller's opaque identity of what runs")
    verb.add_argument('--outcome', type=Path, required=True, help='where the record of how the box ended it goes')
    verb.add_argument('--fingerprint', default=None, help='hash of the code the run depends on (optional)')
    verb.add_argument('--what', default=None, help='the ledger label (default: the command)')
    verb.add_argument(
        '--capacity', type=_capacity, default=None, help="the caller's box size, JSON (default: read this box)"
    )
    verb.add_argument('argv', nargs=argparse.REMAINDER, help='-- then the command')
    return parser


def main(args: Sequence[str] | None = None) -> int:
    """Parse, run, write the record to ``--outcome``; return the child's exit code (the module docstring)."""
    parser = _parser()
    ns = parser.parse_args(args)
    argv = ns.argv[1:] if ns.argv[:1] == ['--'] else ns.argv
    if not argv:
        parser.error('run: no command after --')
    demand = Demand(cpu=ns.cpu, mem_gb=ns.mem_gb, wall_s=ns.wall, seats=dict(ns.seat))
    try:
        outcome = run(
            argv,
            demand,
            ns.what or ' '.join(argv),
            ns.queue_s,
            key=ns.key,
            fingerprint=ns.fingerprint,
            capacity=ns.capacity,
        )
    except Refused as refused:
        write_ended(ns.outcome, refused)
        emit(f'box: refused -- {refused}', err=True)
        return 1
    write_ended(ns.outcome, outcome)
    return outcome.returncode


if __name__ == '__main__':
    sys.exit(main())
