"""RESERVATIONS IN THE FORGE LEDGER -- the same store as the host cards, one comment per lease.

A :class:`~lab_commons.compute.model.Reservation` is a comment on the ``forge hosts`` issue
(:mod:`lab_commons.dev.forgehosts`), beside the cards, found by its marker ``reserve:<scope>`` and
carrying its lease as JSON. Its ID is the forge's comment id. ONE TRANSPORT: cards and leases are read
in one comment listing (:func:`read`), so a reader that sees a host sees who may reserve it.

WHO MAY RESERVE (user ruling 2026-10-13): only the host itself -- the login its own user serves it as,
the author the forge records on its card -- or a login that user designates in the machine file::

    [reserve]
    delegates = ["bob"]     # published on this host's card each tick
    cpu_margin = 1          # cores kept free above what this box's user is using now
    mem_margin = 0.25       # fraction of this box's memory kept free above that

THE CHECK IS THE READER'S. Anyone with a forge credential can post a comment, so a lease counts only when
its FORGE-RECORDED author is the scope's card author or one of the card's delegates; the ``by`` a body
might state is never read. :func:`reserve` and :func:`release` refuse the same writers up front, so a
refused lease is never posted -- but it is every reader's check that makes the rule hold.

THE OWN RESERVE is not a ledger record: it is what this box's user is using now plus a margin
(:func:`own_reserve`), subtracted from the box's supply where it is measured, and lent to nobody.
A machine file says what a host HAS and who may reserve it, never how it is divided.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from lab_commons.compute.model import For, Reservation, Resources
from lab_commons.dev.forgehosts import HostCard, card_of, hosts_issue
from lab_commons.dev.forgepulls import comment_list, marker
from lab_commons.dev.forgework import Client, Comment

__all__ = [
    'DEFAULT_CPU_MARGIN',
    'DEFAULT_MEM_MARGIN',
    'Ask',
    'Entry',
    'Ledger',
    'ReserveRefused',
    'ReserveTable',
    'own_reserve',
    'parse_for',
    'parse_resources',
    'parse_until',
    'read',
    'release',
    'reserve',
    'reserve_table',
]

#: Cores kept free above what the box's own user is using now.
DEFAULT_CPU_MARGIN: Final = 1.0

#: The fraction of the box's memory kept free above what its own user is using now.
DEFAULT_MEM_MARGIN: Final = 0.25

_SUBJECT: Final = 'reserve:'
_BLOCK: Final = re.compile(r'```json\n(?P<json>.*?)\n```', re.DOTALL)
_DURATION: Final = re.compile(r'^(?P<n>\d+(?:\.\d+)?)(?P<unit>[mhd])$')
_UNITS: Final = {'m': 'minutes', 'h': 'hours', 'd': 'days'}


class ReserveRefused(ValueError):
    """A writer the scope's user did not designate, a scope with no card, or no such reservation."""


# --- the record -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ask:
    """A reservation before the forge has recorded who wrote it."""

    scope: str
    resources: Resources
    for_: For
    until: datetime

    def signed(self, by: str) -> Reservation:
        """The reservation *by* writes."""
        return Reservation(self.scope, self.resources, self.for_, self.until, by)


#: The kinds a lease may be for that name one thing (a need-set is the other kind).
_NAMED: Final = ('login', 'host', 'session', 'repo')


def _for_raw(whom: For) -> dict[str, Any]:
    named = {'login': whom.login, 'host': whom.host, 'session': whom.session, 'repo': whom.repo}
    return {kind: value for kind, value in named.items() if value} or {'needs': sorted(whom.needs or ())}


def _for_of(raw: Mapping[str, Any]) -> For:
    if 'needs' in raw:
        return For(needs=frozenset(raw['needs']))
    return For(**{kind: str(raw[kind]) for kind in _NAMED if kind in raw})


@dataclass(frozen=True)
class Entry:
    """One lease on the ledger: the forge's comment *id*, the lease, and why readers refuse it (``''`` = held)."""

    id: int
    reservation: Reservation
    refused: str = ''

    def text(self) -> str:
        """The comment body: the marker, one line a human reads, then the lease as JSON (no writer)."""
        one = self.reservation
        amounts = ','.join(f'{name}={one.resources[name]:g}' for name in sorted(one.resources))
        raw = {
            'scope': one.scope,
            'resources': dict(one.resources),
            'for': _for_raw(one.for_),
            'until': one.until.astimezone(UTC).isoformat(),
        }
        head = f'reserve **{one.scope}** {amounts} for {_spelled(one.for_)} until {raw["until"]}'
        return f'{marker(_SUBJECT + one.scope)}\n{head}\n\n```json\n{json.dumps(raw, indent=1)}\n```'

    def row(self) -> dict[str, Any]:
        """The entry as the data ``forge queue`` shows."""
        one = self.reservation
        return {
            'id': self.id,
            'scope': one.scope,
            'resources': dict(one.resources),
            'for': _spelled(one.for_),
            'until': one.until.isoformat(),
            'by': one.by,
            'refused': self.refused,
        }


def _spelled(whom: For) -> str:
    """The ``--for`` spelling of *whom*."""
    for kind, value in _for_raw(whom).items():
        if kind != 'needs':
            return f'{kind}:{value}'
    return 'needs:' + '+'.join(sorted(whom.needs or ()))


def _entry_of(comment: Comment) -> Entry | None:
    """The lease *comment* carries, signed by its forge-recorded author; ``None`` when it is not one."""
    found = _BLOCK.search(comment.body) if f'lab-commons:{_SUBJECT}' in comment.body else None
    if found is None:
        return None
    raw = json.loads(found['json'])
    ask = Ask(raw['scope'], Resources(raw['resources']), _for_of(raw['for']), datetime.fromisoformat(raw['until']))
    return Entry(comment.id, ask.signed(comment.author))


# --- who may write ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Owner:
    login: str
    delegates: tuple[str, ...]


def _refusal(writer: str, scope: str, owners: Mapping[str, _Owner], verb: str = 'reserve') -> str:
    """Why *writer* may not *verb* *scope*; ``''`` when it may."""
    owner = owners.get(scope)
    if owner is None:
        return f'{scope} has published no host card, so who owns it is unknown'
    if writer and writer in {owner.login, *owner.delegates}:
        return ''
    allowed = ', '.join([owner.login, *owner.delegates])
    return f'{writer or "an unnamed writer"} may not {verb} {scope}: only {allowed} (its user and [reserve] delegates)'


@dataclass(frozen=True)
class Ledger:
    """The hosts issue read once: its *number*, who owns each scope, every lease with its verdict, the cards."""

    number: int | None
    owners: Mapping[str, _Owner]
    entries: tuple[Entry, ...]
    cards: tuple[HostCard, ...] = ()

    def live(self, now: datetime) -> tuple[Entry, ...]:
        """The leases readers honour at *now*: written by an authorised login, and unexpired."""
        return tuple(one for one in self.entries if not one.refused and now < one.reservation.until)


def read(client: Client) -> Ledger:
    """Every card owner and every lease on the hosts issue, each lease judged by its forge-recorded author."""
    number = hosts_issue(client)
    comments = comment_list(client, number) if number is not None else ()
    owners: dict[str, _Owner] = {}
    leases: list[Entry] = []
    cards: list[HostCard] = []
    for comment in comments:
        card = card_of(comment)
        if card is not None:
            cards.append(card)
            owners[card.host] = _Owner(comment.author or card.login, tuple(card.delegates))
        elif (entry := _entry_of(comment)) is not None:
            leases.append(entry)
    judged = tuple(
        Entry(one.id, one.reservation, _refusal(one.reservation.by, one.reservation.scope, owners)) for one in leases
    )
    return Ledger(number, owners, judged, tuple(cards))


def reserve(client: Client, ask: Ask) -> Entry:
    """Post *ask* as the authenticated login; a writer the scope's user did not designate is refused first."""
    writer = client.whoami()
    seen = read(client)
    why = _refusal(writer, ask.scope, seen.owners)
    if why or seen.number is None:
        raise ReserveRefused(why or f'{ask.scope} has published no host card')
    entry = Entry(0, ask.signed(writer))
    posted = client.call('POST', f'{client.repo}/issues/{seen.number}/comments', {'body': entry.text()})
    return Entry(int(posted['id']), entry.reservation)


def release(client: Client, lease_id: int) -> Entry:
    """Delete lease *lease_id*: its writer, the scope's user, or a delegate may; anyone else is refused."""
    writer = client.whoami()
    seen = read(client)
    found = next((one for one in seen.entries if one.id == lease_id), None)
    if found is None:
        msg = f'no reservation {lease_id} on the ledger'
        raise ReserveRefused(msg)
    if writer != found.reservation.by and (why := _refusal(writer, found.reservation.scope, seen.owners, 'release')):
        raise ReserveRefused(why)
    client.call('DELETE', f'{client.repo}/issues/comments/{lease_id}')
    return found


# --- the CLI spelling -------------------------------------------------------------------------------------


def parse_resources(text: str) -> Resources:
    """``cpu=8,mem_gb=32,jmag=1`` into a vector; an empty, unnamed or negative amount is refused."""
    amounts: dict[str, float] = {}
    for item in text.split(','):
        name, sep, value = item.strip().partition('=')
        try:
            amount = float(value)
        except ValueError:
            amount = -1.0
        if not sep or not name or amount < 0:
            msg = f'a resource is name=amount (cpu=8,mem_gb=32), got {item!r}'
            raise ValueError(msg)
        amounts[name] = amount
    return Resources(amounts)


def parse_for(text: str) -> For:
    """``login:X``, ``host:H``, ``session:S``, ``repo:Y`` or ``needs:a+b`` into whom a reservation is for."""
    kind, _, value = text.partition(':')
    if kind == 'needs' and value:
        return For(needs=frozenset(value.split('+')))
    if kind in _NAMED and value:
        return For(**{kind: value})
    msg = f'--for is login:X, host:H, session:S, repo:Y or needs:a+b, got {text!r}'
    raise ValueError(msg)


def parse_until(text: str, now: datetime) -> datetime:
    """An ISO moment, or a duration from *now* (``30m``, ``36h``, ``2d``); a moment already past is refused."""
    found = _DURATION.match(text.strip())
    if found is not None:
        return now + timedelta(**{_UNITS[found['unit']]: float(found['n'])})
    moment = datetime.fromisoformat(text.strip())
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    if moment <= now:
        msg = f'--until {text} is already past (forge time {now.isoformat()})'
        raise ValueError(msg)
    return moment


# --- the machine file and the own reserve -----------------------------------------------------------------


@dataclass(frozen=True)
class ReserveTable:
    """The machine file's ``[reserve]``: who else may reserve this host, and the own reserve's margins."""

    delegates: tuple[str, ...] = ()
    cpu_margin: float = DEFAULT_CPU_MARGIN
    mem_margin: float = DEFAULT_MEM_MARGIN


def reserve_table(table: Mapping[str, Any]) -> ReserveTable:
    """Read ``[reserve]``; an unknown key, a non-list ``delegates`` or a margin out of range is refused."""
    unknown = sorted(set(table) - set(ReserveTable.__dataclass_fields__))
    delegates = table.get('delegates', [])
    cpu = table.get('cpu_margin', DEFAULT_CPU_MARGIN)
    mem = table.get('mem_margin', DEFAULT_MEM_MARGIN)
    bad = [
        *(f'unknown key {key!r}' for key in unknown),
        *(['delegates must be a list of logins'] if not isinstance(delegates, list) else []),
        *(['cpu_margin must be >= 0 cores'] if not isinstance(cpu, int | float) or cpu < 0 else []),
        *(['mem_margin must be a fraction in [0, 1]'] if not isinstance(mem, int | float) or not 0 <= mem <= 1 else []),
    ]
    if bad:
        msg = f'the machine file [reserve]: {"; ".join(bad)}'
        raise ValueError(msg)
    return ReserveTable(tuple(str(one) for one in delegates), float(cpu), float(mem))


def own_reserve(total: Mapping[str, float], in_use: Mapping[str, float], table: ReserveTable) -> Resources:
    """What this box keeps for its own user: what the user uses now plus the margins, never more than the box."""
    wanted = {
        'cpu': in_use.get('cpu', 0.0) + table.cpu_margin,
        'mem_gb': in_use.get('mem_gb', 0.0) + table.mem_margin * total.get('mem_gb', 0.0),
    }
    return Resources({name: min(amount, total.get(name, 0.0)) for name, amount in wanted.items()})
