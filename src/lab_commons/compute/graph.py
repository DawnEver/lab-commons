"""A GRAPH of units -- a calculation, drawn by the unit inputs that are other units' outputs (D16).

The edges are not declared beside the units: an :class:`~lab_commons.compute.unit.Output` input IS the edge, so
the graph cannot disagree with what its units read. Granularity is the caller's: a group of small rotor
positions is one unit, a long march is one unit carrying its own checkpoint.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from lab_commons.compute.unit import Output, Unit

__all__ = ['Graph', 'GraphRefused']


class GraphRefused(ValueError):
    """A graph that cannot run: a duplicate name, an edge to an absent unit or output, or a cycle."""


@dataclass(frozen=True)
class Graph:
    """Named units; :meth:`order` runs every unit after the units it reads."""

    units: Mapping[str, Unit] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse an edge to an absent unit or undeclared output, and a cycle -- before anything runs."""
        bad = [
            f'{name}.{key} reads {ref.unit}:{ref.name}'
            for name, unit in self.units.items()
            for key, ref in unit.inputs.items()
            if isinstance(ref, Output) and (ref.unit not in self.units or ref.name not in self.units[ref.unit].outputs)
        ]
        if bad:
            msg = f'inputs naming no declared output: {"; ".join(bad)}'
            raise GraphRefused(msg)
        self.order()

    def with_unit(self, name: str, unit: Unit) -> Graph:
        """A new graph: these units plus *unit* under *name*."""
        if name in self.units:
            msg = f'unit {name!r} is already in the graph'
            raise GraphRefused(msg)
        return Graph({**self.units, name: unit})

    def dependants(self, name: str) -> frozenset[str]:
        """Every unit that reads *name*'s outputs, directly or through others."""
        found: set[str] = set()
        frontier = {name}
        while frontier:
            frontier = {n for n, u in self.units.items() if u.upstream & frontier} - found
            found |= frontier
        return frozenset(found)

    def order(self) -> tuple[str, ...]:
        """Every unit name, each after its upstream (name order among the ready ones)."""
        done: list[str] = []
        left = dict(self.units)
        while left:
            ready = sorted(n for n, u in left.items() if u.upstream <= set(done))
            if not ready:
                msg = f'a cycle among {sorted(left)}'
                raise GraphRefused(msg)
            done += ready
            for n in ready:
                del left[n]
        return tuple(done)

    def __iter__(self) -> Iterator[str]:
        """The names in :meth:`order`."""
        return iter(self.order())
