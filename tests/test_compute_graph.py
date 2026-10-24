"""D16: a graph's edges are its units' Output inputs; it orders, finds dependants, and refuses what cannot run."""

from __future__ import annotations

import pytest

from lab_commons.compute.graph import Graph, GraphRefused
from lab_commons.compute.model import Demand
from lab_commons.compute.unit import Engine, Environment, Output, Unit

ENGINE = Engine.ours('ours.solve', Environment('a' * 40, 'k' * 24))


def unit(**inputs: object) -> Unit:
    return Unit(ENGINE, 'pkg.mod:fn', inputs, ('out',), Demand(cpu=1, mem_gb=0.1), 60)


def chain() -> Graph:
    return Graph({'c': unit(x=Output('b', 'out')), 'b': unit(x=Output('a', 'out')), 'a': unit(n=1), 'z': unit(n=2)})


def test_order_runs_upstream_first() -> None:
    assert chain().order() == ('a', 'z', 'b', 'c')
    assert tuple(chain()) == chain().order()


def test_dependants_are_transitive() -> None:
    assert chain().dependants('a') == {'b', 'c'}
    assert chain().dependants('z') == frozenset()


def test_a_cycle_is_refused() -> None:
    with pytest.raises(GraphRefused, match='cycle'):
        Graph({'a': unit(x=Output('b', 'out')), 'b': unit(x=Output('a', 'out'))})


def test_an_edge_to_nothing_is_refused() -> None:
    with pytest.raises(GraphRefused, match=r'a\.x reads b:out'):
        Graph({'a': unit(x=Output('b', 'out'))})
    with pytest.raises(GraphRefused, match='missing'):
        Graph({'a': unit(n=1), 'b': unit(x=Output('a', 'missing'))})


def test_with_unit_refuses_a_duplicate_name() -> None:
    g = Graph().with_unit('a', unit(n=1))
    with pytest.raises(GraphRefused, match='already'):
        g.with_unit('a', unit(n=2))
