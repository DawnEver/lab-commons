"""The nouns of the one resource path: a name's KIND, a :class:`Claim`, the :class:`Provider` plug, an :class:`Actual`.

A kind is a property of a resource NAME, declared once here (:data:`KINDS`); it decides only when a held amount
returns. OCCUPANCY returns at run end, CONSUMABLE turns its held cap into a charge of the actual at run end and
returns the rest, PERSISTENT returns only when reaped (its owner is gone). A tool seat is OCCUPANCY by being a
machine file ``[capabilities]`` tool or a tool the family can probe (:data:`~lab_commons.compute.toolchecks.CHECKS`),
and so is a LENT name ``<kind>:<instance>`` (:data:`~lab_commons.box.demand.LENT_NAME`, a JMAG Job System slot).
Any other name is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from lab_commons.box.demand import LENT_NAME, WALL_CEILING_S
from lab_commons.box.machine import provided
from lab_commons.compute.model import Demand, Resources, Supply
from lab_commons.compute.probe import OS_NAMES
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.config import section

__all__ = ['KINDS', 'Actual', 'Claim', 'Kind', 'Provider', 'kind_of']


class Kind(StrEnum):
    """When a held amount returns: at run end, as an unspent remainder at run end, or on reap."""

    OCCUPANCY = 'occupancy'
    CONSUMABLE = 'consumable'
    PERSISTENT = 'persistent'


#: Every fixed resource name and its kind. Tool seats are not listed: they are the machine's ``[capabilities]``.
KINDS: Final[Mapping[str, Kind]] = MappingProxyType(
    {
        **dict.fromkeys(('cpu', 'mem_gb', *OS_NAMES.values()), Kind.OCCUPANCY),
        **dict.fromkeys(('tokens', 'core_h', 'forge_requests', 'submits', 'wall_h', 'money'), Kind.CONSUMABLE),
        **dict.fromkeys(('disk_gb', 'files', 'venvs', 'worktrees'), Kind.PERSISTENT),
    }
)


def kind_of(name: str) -> Kind:
    """The kind of resource *name*: :data:`KINDS`, else OCCUPANCY for a tool seat or a lent name.

    Raises:
        ValueError: *name* has no kind (UNSUPPORTED-RAISES).

    """
    if name in KINDS:
        return KINDS[name]
    if LENT_NAME.match(name) or name in CHECKS or name in provided(section('capabilities')):
        return Kind.OCCUPANCY
    msg = f'resource {name!r} has no kind: not in KINDS, a [capabilities] or checked tool, nor <kind>:<instance>'
    raise ValueError(msg)


@dataclass(frozen=True)
class Claim:
    """What a consumer asks of a scope: its *demand* for at most *wall_s* seconds; *what* and *key* name it."""

    demand: Demand
    wall_s: int
    what: str
    key: str

    def __post_init__(self) -> None:
        """Refuse a claim that books nothing, a wall outside ``(0, WALL_CEILING_S]``, or a name with no kind."""
        object.__setattr__(self, 'demand', Demand(self.demand))
        if not self.demand:
            msg = f'{self.what}: a claim must book something'
            raise ValueError(msg)
        if isinstance(self.wall_s, bool) or not isinstance(self.wall_s, int) or not 0 < self.wall_s <= WALL_CEILING_S:
            msg = f'{self.what}: wall_s must be an int in (0, {WALL_CEILING_S}], got {self.wall_s!r}'
            raise ValueError(msg)
        for name in self.demand:
            kind_of(name)


@dataclass
class Actual:
    """What a run used, filled by its provider's ``enforce`` when the run ends."""

    used: Resources = field(default_factory=Resources)


class Provider(Protocol):
    """The plug: the names it supplies, its supply NOW on a scope, and a run held within a claim."""

    names: frozenset[str]

    def measure(self, scope: str) -> Supply:
        """Supply now by its kind's formula; a consumable's is its window balance (cap less charged)."""
        ...

    def enforce(self, scope: str, claim: Claim) -> AbstractContextManager[Actual]:
        """Run within *claim*; the yielded :class:`Actual` is filled when the context exits."""
        ...
