"""``python -m lab_commons.box run --cpu N --mem-gb G --wall S --queue-s Q --key K --outcome P [...] -- argv``.

Every limit is REQUIRED, exactly as a box claim's wall has no default: a missing one
is an argparse refusal, never a number nobody chose. So is ``--outcome``: the box's decision is the
record written there (:func:`~lab_commons.box.write_ended`) -- the :class:`~lab_commons.box.ProcessEnd`, or the
refusal when nothing ran -- and NEVER the exit code. The exit code is the child's own, and a child may
exit with any number, so a sentinel code would misread a child that happened to choose it. On a refusal
the code is nonzero and means nothing: read the record. Both streams stay the child's.

A PERSON DECLARES THEIR USE with no process under it (ruling 2026-10-25)::

    python -m lab_commons.box claim --cpu N --mem-gb G [--seat jmag=K] --wall 4h --why "<text>"
    python -m lab_commons.box claims [--json]
    python -m lab_commons.box release <id>

The claim is a ledger row owned by the person, gone at its wall deadline or on release; every admission
(a forge hold) gets only the room outside it (:func:`~lab_commons.box.ledger.declare`).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from lab_commons.box.demand import CPU, GB, MEM_GB, WALL_CEILING_S, Capacity, Refused, write_ended
from lab_commons.box.ledger import declare, holdings, release
from lab_commons.box.run import run
from lab_commons.log import emit


def _seat(text: str) -> tuple[str, int]:
    name, sep, count = text.partition('=')
    if not sep or not name or not count.isdigit():
        msg = f'--seat wants name=count, got {text!r}'
        raise argparse.ArgumentTypeError(msg)
    return name, int(count)


_UNIT_S = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def _wall(text: str) -> int:
    """A wall as ``<n>[s|m|h|d]`` (bare: seconds), positive and within the box's wall ceiling."""
    number, unit = (text[:-1], text[-1]) if text[-1:] in _UNIT_S else (text, 's')
    seconds = int(number) * _UNIT_S[unit] if number.isdigit() else 0
    if not 0 < seconds <= WALL_CEILING_S:
        msg = f'--wall wants <n>[s|m|h|d] in (0, {WALL_CEILING_S} s], got {text!r}'
        raise argparse.ArgumentTypeError(msg)
    return seconds


def _capacity(text: str) -> Capacity:
    """The :class:`Capacity` the caller sized its demand against, as :func:`~lab_commons.box.run.command` spells it."""
    try:
        fields = json.loads(text)
        return Capacity(**{**fields, 'watched': tuple(fields.get('watched', ()))})
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
    verb.add_argument(
        '--claim', type=int, default=0, help='a ledger row the caller already took: adopt it, admit nothing again'
    )
    verb.add_argument('argv', nargs=argparse.REMAINDER, help='-- then the command')
    claim = verbs.add_parser('claim', help="declare a person's or a session's use: a held claim, no process")
    claim.add_argument('--cpu', type=int, required=True, help='cores your work will use')
    claim.add_argument('--mem-gb', type=float, required=True, help='memory your work will use')
    claim.add_argument('--seat', type=_seat, action='append', default=[], help='a tool seat, name=count')
    claim.add_argument('--wall', type=_wall, required=True, help='for how long: <n>[s|m|h|d]')
    claim.add_argument('--why', required=True, help='what the claim is for, shown to whoever is refused')
    listing = verbs.add_parser('claims', help='list every claim on this box')
    listing.add_argument('--json', action='store_true', help='one JSON array')
    gone = verbs.add_parser('release', help='drop a claim now')
    gone.add_argument('id', type=int)
    return parser


def _claims(*, as_json: bool) -> int:
    rows = [
        {
            'id': h.id,
            'what': h.what,
            'cpu': h.cpu,
            'mem_gb': h.mem_bytes / GB,
            'seats': dict(h.seats),
            'left_s': round(h.deadline - time.time()),
        }
        for h in holdings()
    ]
    if as_json:
        emit(json.dumps(rows))
        return 0
    for row in rows:
        emit(
            f'{row["id"]:>6}  {row["what"]}  cpu={row["cpu"]} mem_gb={row["mem_gb"]:g} seats={row["seats"]} '
            f'left={row["left_s"]} s'
        )
    return 0


def _release(claim_id: int) -> int:
    held = next((h for h in holdings() if h.id == claim_id), None)
    if held is None:
        emit(f'box: no claim {claim_id} on this box ledger', err=True)
        return 1
    release(held.claim)
    emit(f'box: released claim {claim_id} ({held.what})')
    return 0


def main(args: Sequence[str] | None = None) -> int:
    """Parse, run, write the record to ``--outcome``; return the child's exit code (the module docstring)."""
    parser = _parser()
    ns = parser.parse_args(args)
    if ns.verb == 'claim':
        demand = {**dict(ns.seat), CPU: ns.cpu, MEM_GB: ns.mem_gb}
        try:
            made = declare(demand, ns.wall, ns.why, owner=getpass.getuser())
        except ValueError as bad:
            parser.error(str(bad))
        emit(f'box: claim {made.id} held until {time.strftime("%Y-%m-%d %H:%M", time.localtime(made.deadline))}')
        return 0
    if ns.verb == 'claims':
        return _claims(as_json=ns.json)
    if ns.verb == 'release':
        return _release(ns.id)
    argv = ns.argv[1:] if ns.argv[:1] == ['--'] else ns.argv
    if not argv:
        parser.error('run: no command after --')
    demand = {**dict(ns.seat), CPU: ns.cpu, MEM_GB: ns.mem_gb}
    try:
        outcome = run(
            argv,
            demand,
            ns.wall,
            ns.what or ' '.join(argv),
            ns.queue_s,
            key=ns.key,
            fingerprint=ns.fingerprint,
            capacity=ns.capacity,
            claim=ns.claim,
        )
    except Refused as refused:
        write_ended(ns.outcome, refused)
        emit(f'box: refused -- {refused}', err=True)
        return 1
    write_ended(ns.outcome, outcome)
    return outcome.returncode


if __name__ == '__main__':
    sys.exit(main())
