"""Inside one island: rank its nodes into columns, then order each column's rows to cross less."""

from __future__ import annotations

from collections import defaultdict, deque
from statistics import median
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

__all__ = [
    'ORDERING_SWEEPS',
    'arrange_island',
    'causal_ranks',
    'connected_islands',
    'physical_ranks',
    'straight_crossings',
]

#: Barycentric sweeps a refined island gets; the ordering with the fewest estimated crossings is kept.
ORDERING_SWEEPS = 24
#: Sweeps a plain island gets; the last ordering is kept.
PLAIN_SWEEPS = 4

_Positions = dict[str, tuple[float, float]]
_Columns = dict[int, list[str]]


class _Sized(Protocol):
    width: float
    height: float
    source: bool


def connected_islands(keys: Iterable[str], adjacency: Mapping[str, set[str]]) -> Iterator[set[str]]:
    """The connected components of ``adjacency`` over ``keys``, each seeded at its least key."""
    remaining = set(keys)
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group, queue = {seed}, deque([seed])
        while queue:
            for neighbor in sorted(adjacency[queue.popleft()] & remaining):
                remaining.remove(neighbor)
                group.add(neighbor)
                queue.append(neighbor)
        yield group


def physical_ranks(group: set[str], adjacency: Mapping[str, set[str]], items: Mapping[str, _Sized]) -> dict[str, int]:
    """Breadth-first distance from the island's sources (else its least key)."""
    seeds = sorted(k for k in group if items[k].source) or [min(group)]
    ranks = dict.fromkeys(seeds, 0)
    queue = deque(seeds)
    while queue:
        key = queue.popleft()
        for neighbor in sorted(adjacency[key]):
            if neighbor not in ranks:
                ranks[neighbor] = ranks[key] + 1
                queue.append(neighbor)
    return ranks


def _back_edges(roots: Sequence[str], forward: Mapping[str, set[str]]) -> set[tuple[str, str]]:
    """Deterministic iterative DFS from ``roots`` in order: every edge into a node still on the stack."""
    color: dict[str, int] = {}
    feedback = set()
    for root in roots:
        if root in color:
            continue
        color[root] = 1
        stack = [(root, iter(sorted(forward[root])))]
        while stack:
            key, neighbors = stack[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                color[key] = 2
                stack.pop()
            elif color.get(neighbor) == 1:
                feedback.add((key, neighbor))
            elif neighbor not in color:
                color[neighbor] = 1
                stack.append((neighbor, iter(sorted(forward[neighbor]))))
    return feedback


def _longest_path(group: set[str], forward: Mapping[str, set[str]]) -> dict[str, int]:
    indegree = dict.fromkeys(group, 0)
    for targets in forward.values():
        for target in targets:
            indegree[target] += 1
    ranks = dict.fromkeys(group, 0)
    queue = deque(sorted(k for k in group if indegree[k] == 0))
    while queue:
        key = queue.popleft()
        for target in sorted(forward[key]):
            ranks[target] = max(ranks[target], ranks[key] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return ranks


def causal_ranks(
    group: set[str], links: Iterable[tuple[str, str]], items: Mapping[str, _Sized]
) -> tuple[dict[str, int], set[tuple[str, str]]]:
    """Longest-path ranks along the links, and the feedback edges set aside to make them acyclic.

    Deterministic DFS back edges form feedback lanes, not removed links. Unlike collapsing a whole feedback
    SCC into one column, the remaining DAG keeps its readable forward pipeline.
    """
    forward = {k: set() for k in group}
    incoming = dict.fromkeys(group, 0)
    for source, target in links:
        if source in group and target in group:
            forward[source].add(target)
            incoming[target] += 1
    feedback = _back_edges(sorted(group, key=lambda k: (not items[k].source, incoming[k] != 0, k)), forward)
    for source, target in feedback:
        forward[source].remove(target)
    ranks = _longest_path(group, forward)
    # A node with no input INSIDE its island -- a reference, or one fed from the physical band -- belongs
    # beside its earliest consumer, not in one tall input column. Never move a node with incoming edges.
    for key in sorted(group):
        if incoming[key] == 0 and forward[key]:
            ranks[key] = min(ranks[target] for target in forward[key]) - 1
    return ranks, feedback


def _stack(columns: _Columns, items: Mapping[str, _Sized], gap: float) -> tuple[_Positions, float, float]:
    """Center each column's nodes vertically: ``(positions, width, height)`` of the island."""
    heights = {column: sum(items[k].height for k in keys) + gap * (len(keys) - 1) for column, keys in columns.items()}
    height = max(heights.values())
    positions, x = {}, 0.0
    for column in sorted(columns):
        keys = columns[column]
        width = max(items[k].width for k in keys)
        y = (height - heights[column]) / 2
        for key in keys:
            positions[key] = (x + width / 2, y + items[key].height / 2)
            y += items[key].height + gap
        x += width + gap
    return positions, x - gap, height


def _at(x: float, line: tuple[float, float, float, float]) -> float:
    xs, ys, xe, ye = line
    return ys if xe == xs else ys + (ye - ys) * (x - xs) / (xe - xs)


def straight_crossings(positions: _Positions, edges: Iterable[tuple[str, str]]) -> int:
    """Crossings of the island's edges drawn as straight center-to-center lines: the ordering's objective."""
    lines = []
    for a, b in edges:
        (xa, ya), (xb, yb) = positions[a], positions[b]
        if xa > xb:
            (xa, ya), (xb, yb) = (xb, yb), (xa, ya)
        lines.append(((xa, ya, xb, yb), {a, b}))
    count = 0
    for i, (one, ends) in enumerate(lines):
        for other, other_ends in lines[i + 1 :]:
            low, high = max(one[0], other[0]), min(one[2], other[2])
            if ends & other_ends or low >= high:
                continue
            count += (_at(low, one) - _at(low, other)) * (_at(high, one) - _at(high, other)) < 0
    return count


def _sweep_column(
    columns: _Columns, column: int, rows: dict[str, int], adjacency: Mapping[str, set[str]], ranks: Mapping[str, int]
) -> None:
    def score(key: str) -> tuple[float, str]:
        neighbors = [rows[k] for k in adjacency[key] if ranks[k] != column]
        return (median(neighbors) if neighbors else rows[key], key)

    columns[column].sort(key=score)
    rows.update({key: row for row, key in enumerate(columns[column])})


def arrange_island(
    group: set[str],
    ranks: Mapping[str, int],
    adjacency: Mapping[str, set[str]],
    items: Mapping[str, _Sized],
    gap: float,
    *,
    refine: bool = False,
) -> tuple[_Positions, float, float]:
    """Order each column's rows by alternating barycentric sweeps (keys break every tie), then stack them.

    Plain: ``PLAIN_SWEEPS`` sweeps, the last ordering kept. ``refine``: ``ORDERING_SWEEPS`` sweeps keeping the
    ordering with the fewest estimated crossings, then adjacent-swap transposition while a swap lowers it.
    """
    columns: _Columns = defaultdict(list)
    for key in sorted(group):
        columns[ranks[key]].append(key)
    edges = sorted({tuple(sorted((a, b))) for a in group for b in adjacency[a] if b in group and ranks[a] != ranks[b]})
    best = None
    if refine:
        best = (straight_crossings(_stack(columns, items, gap)[0], edges), {c: list(k) for c, k in columns.items()})
    for sweep in range(ORDERING_SWEEPS if refine else PLAIN_SWEEPS):
        rows = {key: row for keys in columns.values() for row, key in enumerate(keys)}
        for column in sorted(columns, reverse=bool(sweep % 2)):
            _sweep_column(columns, column, rows, adjacency, ranks)
        if refine:
            crossings = straight_crossings(_stack(columns, items, gap)[0], edges)
            if crossings < best[0]:
                best = (crossings, {c: list(k) for c, k in columns.items()})
    return _stack(_transposed(*best, edges, items, gap) if refine else columns, items, gap)


def _transposed(
    crossings: int, columns: _Columns, edges: Sequence[tuple[str, str]], items: Mapping[str, _Sized], gap: float
) -> _Columns:
    """Swap vertical neighbors while a swap strictly lowers the estimate (deterministic left-to-right order)."""
    improved = True
    while improved and crossings:
        improved = False
        for column in sorted(columns):
            keys = columns[column]
            for row in range(len(keys) - 1):
                keys[row], keys[row + 1] = keys[row + 1], keys[row]
                trial = straight_crossings(_stack(columns, items, gap)[0], edges)
                if trial < crossings:
                    crossings, improved = trial, True
                else:
                    keys[row], keys[row + 1] = keys[row + 1], keys[row]
    return columns
