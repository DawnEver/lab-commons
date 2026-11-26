"""Layered placement arranges topology, never names or incoming coordinates; equal input, equal output."""

import random
from itertools import combinations, pairwise

import pytest

from lab_commons.graph_layout import Net, Node, _ordering, place_graph, stack_beside, stub_center


def _overlap(nodes, positions) -> bool:
    for a, b in combinations(nodes, 2):
        (x, y), (u, v) = positions[a.key], positions[b.key]
        if abs(x - u) < (a.width + b.width) / 2 and abs(y - v) < (a.height + b.height) / 2:
            return True
    return False


def test_planted_the_overlap_checker_can_say_no() -> None:
    nodes = (Node('a', 10, 10), Node('b', 10, 10))
    assert _overlap(nodes, {'a': (0, 0), 'b': (5, 5)})
    assert not _overlap(nodes, {'a': (0, 0), 'b': (10, 0)})


def test_physical_flow_and_causal_flow_have_distinct_bands() -> None:
    nodes = [
        Node('source', 96, 56, source=True),
        Node('sink', 96, 56),
        Node('first', 96, 56, 'causal'),
        Node('second', 96, 56, 'causal'),
    ]
    result = place_graph(nodes, [Net('n', ('source', 'sink'))], [('first', 'second'), ('second', 'sink')])
    p = result.positions
    assert p['source'][0] < p['sink'][0]
    assert p['first'][0] < p['second'][0]
    assert max(p[k][1] for k in ('source', 'sink')) < min(p[k][1] for k in ('first', 'second'))


def test_disconnected_nodes_pack_instead_of_forming_a_diagonal_or_tower() -> None:
    nodes = [Node(str(i), 96, 56, 'causal') for i in range(100)]
    result = place_graph(nodes)
    assert result.width / result.height < 3
    assert result.height / result.width < 3
    assert len({x for x, _y in result.positions.values()}) < 20
    assert not _overlap(nodes, result.positions)


@pytest.mark.parametrize('seed', range(4))
def test_placement_is_deterministic_never_overlaps_and_ignores_input_order(seed) -> None:
    rng = random.Random(seed)  # noqa: S311 -- a reproducible graph, not a secret
    nodes = [
        Node(f'n{i}', rng.choice((20, 40)), rng.choice((10, 30)), rng.choice(('physical', 'causal'))) for i in range(25)
    ]
    nets = [Net(f'e{i}', tuple(sorted({n.key for n in rng.sample(nodes, 3)}))) for i in range(8)]
    links = sorted({(a.key, b.key) for a, b in (rng.sample(nodes, 2) for _ in range(15))})
    result = place_graph(nodes, nets, links, refine=True)
    assert result == place_graph(nodes, nets, links, refine=True)
    assert result == place_graph(reversed(nodes), reversed(nets), reversed(links), refine=True)
    assert not _overlap(nodes, result.positions)


def test_feedback_is_layout_metadata_not_removed_connectivity() -> None:
    nodes = [Node(k, 96, 56, 'causal') for k in ('a', 'b', 'c')]
    links = [('a', 'b'), ('b', 'c'), ('c', 'a')]
    result = place_graph(nodes, directed_links=links)
    assert result.positions['a'][0] < result.positions['b'][0] < result.positions['c'][0]
    assert result.feedback_edges == (('c', 'a'),)
    assert result == place_graph(reversed(nodes), directed_links=reversed(links))


def test_reference_nets_do_not_collapse_source_ranks() -> None:
    nodes = [Node('s', 96, 56, source=True), Node('m', 96, 56), Node('t', 96, 56)]
    nets = [Net('in', ('s', 'm')), Net('out', ('m', 't')), Net('ret', ('s', 't'), reference=True)]
    result = place_graph(nodes, nets)
    assert result.ranks == {'s': 0, 'm': 1, 't': 2}
    assert result == place_graph(reversed(nodes), reversed(nets))


def test_root_inputs_stay_near_their_first_consumer_not_in_one_tower() -> None:
    chain = ('a', 'b', 'c', 'd', 'sink')
    roots = tuple(f'in{i}' for i in range(4))
    nodes = [Node(k, 96, 56, 'causal', source=k in roots) for k in (*chain, *roots)]
    links = list(pairwise(chain)) + [(roots[i], chain[i + 1]) for i in range(4)]
    result = place_graph(nodes, directed_links=links)
    assert {k: result.ranks[k] for k in chain} == dict(zip(chain, range(5), strict=True))
    assert [result.ranks[k] for k in roots] == [0, 1, 2, 3]
    unclassified = [Node(k, 96, 56, 'causal') for k in (*chain, *roots)]
    assert place_graph(unclassified, directed_links=links).ranks == result.ranks


def test_source_metadata_does_not_retime_a_node_with_real_incoming_edges() -> None:
    nodes = [Node(k, 96, 56, 'causal', source=k == 'mid') for k in ('root', 'mid', 'a', 'b', 'sink')]
    links = [('root', 'mid'), ('root', 'a'), ('a', 'b'), ('b', 'sink'), ('mid', 'sink')]
    result = place_graph(nodes, directed_links=links)
    assert result.ranks['mid'] == 1
    assert result.ranks['sink'] == 3


@pytest.mark.parametrize('case', ['size', 'duplicate', 'net', 'link', 'section', 'kind'])
def test_invalid_graphs_are_refused_by_name(case) -> None:
    nodes, nets, links = {
        'size': ([Node('a', 0, 56)], [], []),
        'duplicate': ([Node('a', 96, 56)] * 2, [], []),
        'net': ([Node('a', 96, 56)], [Net('n', ('a', 'missing'))], []),
        'link': ([Node('a', 96, 56)], [], [('a', 'missing')]),
        'section': ([Node('a', 96, 56, 'power')], [], []),
        'kind': ([Node('a', 96, 56, kind='switch')], [], []),
    }[case]
    with pytest.raises(ValueError, match=r'layout requires|layout edge'):
        place_graph(nodes, nets, links)


def test_the_leg_motif_uses_physical_rails_not_keys_or_incoming_coordinates() -> None:
    nodes = (
        Node('src', 46, 46, source=True),
        Node('ref', 12, 12, kind='reference'),
        Node('z-up', 20, 20, kind='leg', label='A up'),
        Node('a-down', 20, 20, kind='leg', label='A down'),
        Node('a-up', 20, 20, kind='leg', label='B up'),
        Node('z-down', 20, 20, kind='leg', label='B down'),
        Node('load', 46, 46),
    )
    nets = (
        Net('high', ('src', 'z-up', 'a-up')),
        Net('low', ('src', 'a-down', 'z-down', 'ref'), reference=True),
        Net('mid-a', ('z-up', 'a-down', 'load')),
        Net('mid-b', ('a-up', 'z-down', 'load')),
    )
    result = place_graph(nodes, nets, gap=16)
    p = result.positions
    assert p['z-up'][0] == p['a-down'][0] < p['a-up'][0] == p['z-down'][0]
    assert p['z-up'][1] == p['a-up'][1] < p['a-down'][1] == p['z-down'][1]
    assert p['src'][0] < p['z-up'][0] < p['load'][0]
    assert p['ref'][0] == p['src'][0]
    assert p['ref'][1] > p['src'][1]
    assert set(p) == {n.key for n in nodes}
    assert not _overlap(nodes, p)
    assert result == place_graph(reversed(nodes), reversed(nets), gap=16)


def test_non_physical_nets_cannot_invent_a_leg_motif() -> None:
    nodes = (Node('src', 20, 20, source=True), Node('up', 20, 20, kind='leg'), Node('down', 20, 20, kind='leg'))
    nets = (
        Net('rail', ('src', 'up')),
        Net('ret', ('src', 'down'), reference=True),
        Net('c', ('up', 'down'), physical=False),
    )
    result = place_graph(nodes, nets)
    assert result.positions['up'][0] != result.positions['down'][0]


def test_a_physical_island_sits_above_the_causal_nodes_linked_to_it() -> None:
    chain = [f's{i}' for i in range(6)]
    nodes = [Node('a', 96, 56), Node('z', 96, 56)] + [Node(k, 96, 56, 'causal') for k in chain]
    result = place_graph(nodes, (), [*pairwise(chain), ('s5', 'a'), ('s0', 'z')])
    x = {k: p[0] for k, p in result.positions.items()}
    assert abs(x['a'] - x['s5']) < 96
    assert abs(x['z'] - x['s0']) < 96
    assert max(result.positions[k][1] for k in 'az') < min(result.positions[k][1] for k in chain)


def test_sliding_a_physical_island_never_widens_the_drawing() -> None:
    chain = [f's{i}' for i in range(3)]
    nodes = [Node('a', 400, 56), *(Node(k, 96, 56, 'causal') for k in chain)]
    still = place_graph(nodes, (), list(pairwise(chain)))
    assert place_graph(nodes, (), [*pairwise(chain), ('s2', 'a')]).width == still.width


def test_a_band_takes_one_row_when_that_makes_the_smaller_drawing() -> None:
    physical = [Node(f'p{i}', 100, 40) for i in range(3)]
    chain = [Node(f's{i}', 30, 60, 'causal') for i in range(6)]
    links = list(pairwise(f's{i}' for i in range(6)))
    result = place_graph([*physical, *chain], directed_links=links, gap=10, margin=10, band_gap=10)
    assert len({result.positions[f'p{i}'][1] for i in range(3)}) == 1


def test_a_stub_sits_off_the_port_it_serves() -> None:
    assert stub_center((10, 10), (-1, 0), (6, 4), stub=2) == (5, 10)
    assert stub_center((10, 10), (0, 1), (6, 4), stub=2) == (10, 14)


def test_islands_beside_ports_keep_port_order_without_overlap_centered_on_the_ports() -> None:
    placed = stack_beside(
        [('p0', (100, 2), (-1, 0), (8, 6)), ('p2', (100, 6), (-1, 0), (8, 6)), ('p1', (100, 4), (-1, 0), (8, 6))],
        clearance=10,
        gap=1,
    )
    assert placed == {'p0': (86, -3), 'p1': (86, 4), 'p2': (86, 11)}


def _estimate(seed: int, *, refine: bool) -> int:
    rng = random.Random(seed)  # noqa: S311 -- a reproducible graph, not a secret
    keys = [f'k{i:02d}' for i in range(30)]
    ranks = {k: i % 6 for i, k in enumerate(keys)}
    adjacency = {k: set() for k in keys}
    while sum(map(len, adjacency.values())) < 90:
        a, b = rng.sample(keys, 2)
        if abs(ranks[a] - ranks[b]) == 1:
            adjacency[a].add(b)
            adjacency[b].add(a)
    nodes = {k: Node(k, 10, 10) for k in keys}
    edges = sorted({tuple(sorted((a, b))) for a in keys for b in adjacency[a]})
    arranged = _ordering.arrange_island(set(keys), ranks, adjacency, nodes, 4, refine=refine)
    return _ordering.straight_crossings(arranged[0], edges)


@pytest.mark.parametrize('seed', range(5))
def test_refinement_never_leaves_more_crossings_than_the_plain_sweeps(seed) -> None:
    assert _estimate(seed, refine=True) <= _estimate(seed, refine=False)


def test_refinement_strictly_helps_on_a_measured_island() -> None:
    assert _estimate(0, refine=True) < _estimate(0, refine=False)
