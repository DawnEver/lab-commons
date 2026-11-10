r"""The cluster catalog: each architecture's node classes, as data, and the lanes a whole node is cut into.

THE MECHANISM IS CLUSTER-AGNOSTIC; A CLUSTER IS DATA. A job states its shape relative to the architecture
(``[task] lane_domains``: one lane is that many L3 domains); this module derives, for the cluster a plan
places on, how many lanes a node holds, which CPU ids and NUMA nodes each lane gets. A grant names its
architecture (``[[hpc.grant]] architecture``); every account on one cluster names the same entry.

ONE FILE PER USER, SHARED BY EVERY WORKSTATION. The catalog is :func:`catalog_path` -- ``hpc/clusters.toml``
beside the machine config (:func:`lab_commons.config.config_path`, so ``$LAB_COMMONS_CONFIG`` moves both).
The repo ships no real cluster: the one file lives in the user's OneDrive and each workstation LINKS its
directory into place. On Windows that link is a directory junction (OneDrive replaces files on sync, and a
file symlink needs privilege)::

    mklink /J "%USERPROFILE%\.config\lab-commons\hpc" "%OneDrive%\Sync\hpc\lab-commons"

and elsewhere ``ln -s <OneDrive>/Sync/hpc/lab-commons ~/.config/lab-commons/hpc``.

SLURM STAYS THE AUTHORITY FOR WHAT IT REPORTS. The catalog records only what Slurm cannot say -- sockets, L3
domains, the CPU numbering inside a node -- with its measurement's provenance in a comment. CPU totals are
checked against Slurm at plan time; memory and availability are never written here. A node class whose
topology was not measured is not written either: it is never guessed.

The format -- a table per architecture, a table per node class::

    # uon-ada standard nodes: lscpu on a compute node, 2026-11-06 (AMD Genoa, L3-as-NUMA).
    [uon-ada.class.standard]
    partitions = ["defq", "devq", "shortq"]    # the partitions whose nodes this class describes
    sockets = 2                                # domains are listed socket by socket, evenly
    domains = [                                # one entry per L3 domain: its CPU ids and its NUMA node
      { cpus = "0-5", numa = 0 },
      { cpus = "6-11", numa = 1 },
      # ... one line per domain, 16 in all
    ]

A lane is ``lane_domains`` consecutive domains; lanes tile the node and never straddle a socket unless they
are whole sockets (:meth:`NodeClass.lanes`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from lab_commons.config import config_path

__all__ = [
    'Architecture',
    'Domain',
    'Lane',
    'NodeClass',
    'architecture',
    'catalog_path',
    'cpu_ids',
    'cpulist',
    'load_catalog',
]

#: The catalog, relative to the machine config's directory.
CATALOG: Final = Path('hpc') / 'clusters.toml'

_CLASS_KEYS: Final = frozenset({'partitions', 'sockets', 'domains'})
_DOMAIN_KEYS: Final = frozenset({'cpus', 'numa'})


def catalog_path() -> Path:
    """The user's cluster catalog, beside the machine config."""
    return config_path().parent / CATALOG


def cpu_ids(cpus: str) -> tuple[int, ...]:
    """The CPU ids of a Linux cpulist (``"0-2,7"``)."""
    ids: list[int] = []
    for part in cpus.split(','):
        first, _, last = part.strip().partition('-')
        ids += range(int(first), int(last or first) + 1)
    return tuple(ids)


def cpulist(ids: Iterable[int]) -> str:
    """*ids* as the shortest Linux cpulist."""
    runs: list[list[int]] = []
    for n in sorted(ids):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ','.join(f'{a}' if a == b else f'{a}-{b}' for a, b in runs)


@dataclass(frozen=True)
class Domain:
    """One L3 domain: its CPU ids and the NUMA node its memory is local to."""

    cpus: tuple[int, ...]
    numa: int


@dataclass(frozen=True)
class Lane:
    """What one lane is bound to: its CPU ids and NUMA nodes, each a cpulist (``taskset -c``, ``--mem-bind``)."""

    cpus: str
    mems: str


@dataclass(frozen=True)
class NodeClass:
    """One kind of node of an architecture, and the partitions holding it."""

    name: str
    partitions: tuple[str, ...]
    sockets: int
    domains: tuple[Domain, ...]

    @property
    def cpus(self) -> int:
        """Every CPU of the node -- checked against Slurm's count at plan time."""
        return sum(len(d.cpus) for d in self.domains)

    def lanes(self, lane_domains: int) -> tuple[Lane, ...]:
        """The node cut into lanes of *lane_domains* consecutive domains; refused unless they tile it by socket."""
        per_socket = len(self.domains) // self.sockets
        tiles = lane_domains >= 1 and (
            per_socket % lane_domains == 0
            or (lane_domains % per_socket == 0 and self.sockets % (lane_domains // per_socket) == 0)
        )
        if not tiles:
            msg = (
                f'[task] lane_domains = {lane_domains} does not tile a {self.name} node '
                f'({self.sockets} sockets x {per_socket} L3 domains): a lane divides a socket or is whole sockets'
            )
            raise ValueError(msg)
        groups = [self.domains[i : i + lane_domains] for i in range(0, len(self.domains), lane_domains)]
        return tuple(
            Lane(cpus=cpulist(c for d in g for c in d.cpus), mems=cpulist({d.numa for d in g})) for g in groups
        )


@dataclass(frozen=True)
class Architecture:
    """One cluster architecture: its node classes."""

    name: str
    classes: tuple[NodeClass, ...]

    def node_class(self, partitions: Sequence[str]) -> NodeClass | None:
        """The class holding the first of *partitions* any class describes; ``None`` when none does."""
        for partition in partitions:
            for cls in self.classes:
                if partition in cls.partitions:
                    return cls
        return None


def _domain(raw: object, where: str) -> Domain:
    if not isinstance(raw, dict) or set(raw) != _DOMAIN_KEYS:
        msg = f'{where}: a domain is {{ cpus = "<cpulist>", numa = <id> }}, got {raw!r}'
        raise ValueError(msg)
    return Domain(cpus=cpu_ids(raw['cpus']), numa=int(raw['numa']))


def _node_class(name: str, raw: dict[str, Any], where: str) -> NodeClass:
    if set(raw) != _CLASS_KEYS:
        msg = f'{where}: a node class declares exactly {sorted(_CLASS_KEYS)}, got {sorted(raw)}'
        raise ValueError(msg)
    domains = tuple(_domain(d, where) for d in raw['domains'])
    sockets = int(raw['sockets'])
    if sockets < 1 or not domains or len(domains) % sockets:
        msg = f'{where}: {len(domains)} domains do not split evenly over {sockets} sockets'
        raise ValueError(msg)
    ids = [c for d in domains for c in d.cpus]
    if len(ids) != len(set(ids)):
        msg = f'{where}: a CPU id is listed twice'
        raise ValueError(msg)
    return NodeClass(name, tuple(raw['partitions']), sockets, domains)


def load_catalog(path: Path | None = None) -> dict[str, Architecture]:
    """Every architecture of the catalog at *path* (default :func:`catalog_path`), by name."""
    source = catalog_path() if path is None else path
    if not source.is_file():
        msg = (
            f'no cluster catalog at {source}: link the directory to the shared one, on Windows a junction '
            f'(mklink /J "{source.parent}" "<OneDrive>\\Sync\\hpc\\lab-commons"); see lab_commons.hpc.topology'
        )
        raise FileNotFoundError(msg)
    raw = tomllib.loads(source.read_text(encoding='utf-8'))
    found = {}
    for arch, table in raw.items():
        if set(table) != {'class'}:
            msg = f'{source}: [{arch}] holds only [{arch}.class.<name>] tables, got {sorted(table)}'
            raise ValueError(msg)
        classes = tuple(_node_class(n, c, f'{source} [{arch}.class.{n}]') for n, c in table['class'].items())
        held = [p for c in classes for p in c.partitions]
        if len(held) != len(set(held)):
            msg = f'{source}: [{arch}] a partition is claimed by two classes'
            raise ValueError(msg)
        found[arch] = Architecture(arch, classes)
    return found


def architecture(name: str) -> Architecture:
    """The catalog's architecture *name*; refused, naming the catalog, when it has none."""
    catalog = load_catalog()
    if name not in catalog:
        msg = f'the cluster catalog {catalog_path()} has no architecture {name!r}; it has {sorted(catalog)}'
        raise ValueError(msg)
    return catalog[name]
