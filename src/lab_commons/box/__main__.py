"""``python -m lab_commons.box run --cpu N --mem-gb G --wall S --queue-s Q --key K [--fingerprint F] -- argv``.

Every limit is REQUIRED, exactly as :class:`~lab_commons.box.Demand` has no defaults: a missing one
is an argparse refusal, never a number nobody chose. The exit code is the child's own, :data:`KILLED`
when the box killed it or :data:`REFUSED` when it never ran; the
:class:`~lab_commons.box.Outcome` is one JSON line on stderr, so stdout stays the child's.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from lab_commons.box.demand import Demand, Refused
from lab_commons.box.run import run
from lab_commons.log import emit

#: Exit code when the box refused the run (no room in time, or no enforcer): sysexits EX_UNAVAILABLE.
REFUSED = 69

#: Exit code when the box killed the tree (memory or wall): 128 + SIGKILL, the code a shell expects.
KILLED = 137


def _seat(text: str) -> tuple[str, int]:
    name, sep, count = text.partition('=')
    if not sep or not name or not count.isdigit():
        msg = f'--seat wants name=count, got {text!r}'
        raise argparse.ArgumentTypeError(msg)
    return name, int(count)


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
    verb.add_argument('--fingerprint', default=None, help='hash of the code the run depends on (optional)')
    verb.add_argument('--what', default=None, help='the ledger label (default: the command)')
    verb.add_argument('argv', nargs=argparse.REMAINDER, help='-- then the command')
    return parser


def main(args: Sequence[str] | None = None) -> int:
    """Parse, run, report the Outcome on stderr; return the exit code the module docstring names."""
    parser = _parser()
    ns = parser.parse_args(args)
    argv = ns.argv[1:] if ns.argv[:1] == ['--'] else ns.argv
    if not argv:
        parser.error('run: no command after --')
    demand = Demand(cpu=ns.cpu, mem_gb=ns.mem_gb, wall_s=ns.wall, seats=dict(ns.seat))
    try:
        outcome = run(argv, demand, ns.what or ' '.join(argv), ns.queue_s, key=ns.key, fingerprint=ns.fingerprint)
    except Refused as refused:
        emit(f'box: refused -- {refused}', err=True)
        return REFUSED
    emit(json.dumps(asdict(outcome)), err=True)
    return outcome.returncode if outcome.reason == 'exit' else KILLED


if __name__ == '__main__':
    sys.exit(main())
