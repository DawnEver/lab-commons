"""HOW THIS MACHINE'S NETWORK REACHES EACH CLUSTER -- checked before any ssh to a login host.

A cluster's login nodes take ssh DIRECTLY from some machines (its site network) and only over a VPN from every
other one. That is a fact of the machine, declared once in its host file of the config source and rendered into
the machine file as ``[hpc.cluster_reach.<cluster>]``::

    [hpc.cluster_reach.<cluster>]
    reach = "vpn"                    # one of REACH: "direct" | "vpn"
    direct = ["host-a", "host-b"]    # the machines that reach it directly (derived by the renderer)

:func:`require` runs before every ssh to a declared cluster's login host (:func:`lab_commons.hpc.cluster.ssh_runner`):
a cluster this machine declares no reach for is REFUSED naming the declaration (:class:`ReachRefused`); a ``vpn``
machine's silent host is reported by :func:`unreachable` naming the VPN and the direct hosts. A host no declared
cluster lists is not a cluster's and is never checked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.config import config_path, section

__all__ = ['DECLARATION', 'REACH', 'TABLE', 'Reach', 'ReachRefused', 'require', 'unreachable']

#: How a machine reaches a cluster: ``direct`` -- its login hosts take ssh from this machine's own network;
#: ``vpn`` -- only once the cluster site's VPN is connected.
REACH: Final = ('direct', 'vpn')
#: The machine-file key under ``[hpc]`` holding each cluster's reach.
TABLE: Final = 'cluster_reach'
#: Where the fact is declared: the host file of the config source, rendered into :data:`TABLE`.
DECLARATION: Final = "[dev] hpc_reach in this machine's host file of the config source"


class ReachRefused(PermissionError):
    """An ssh to a cluster this machine declares no reach for; the message names the declaration."""


@dataclass(frozen=True)
class Reach:
    """How this machine reaches *cluster*, and the machines that reach it directly."""

    cluster: str
    reach: str
    direct: tuple[str, ...] = ()


def _cluster_of(host: str, hpc: Mapping[str, Any]) -> str | None:
    clusters = hpc.get('clusters', {})
    found = [name for name, table in clusters.items() if isinstance(table, dict) and host in table.get('hosts', ())]
    return found[0] if found else None


def require(dest: str, table: Mapping[str, Any] | None = None) -> Reach | None:
    """How this machine reaches the cluster whose login host *dest* (``[user@]host``) is; ``None``: no cluster's.

    *table* is the machine file's ``[hpc]`` (default: this machine's).

    Raises:
        ReachRefused: *dest* is a declared cluster's login host and this machine declares no reach for it.
        ValueError: the declared reach is not one of :data:`REACH`.

    """
    hpc = section('hpc') if table is None else table
    cluster = _cluster_of(dest.rpartition('@')[2], hpc)
    if cluster is None:
        return None
    row = hpc.get(TABLE, {}).get(cluster)
    if not isinstance(row, dict):
        msg = (
            f'{dest} is a login host of {cluster} and this machine declares no reach for it: declare '
            f'{DECLARATION} = {{ {cluster} = "direct" | "vpn" }} (rendered as {config_path()} '
            f'[hpc.{TABLE}.{cluster}]), then re-render the machine file; no ssh was attempted'
        )
        raise ReachRefused(msg)
    if row.get('reach') not in REACH:
        msg = f'{config_path()} [hpc.{TABLE}.{cluster}] reach = {row.get("reach")!r} is not one of {list(REACH)}'
        raise ValueError(msg)
    return Reach(cluster, row['reach'], tuple(row.get('direct', ())))


def unreachable(reach: Reach | None, said: str) -> str:
    """What a silent login host means: over a VPN, the VPN and the direct hosts; otherwise *said* as it is."""
    if reach is None or reach.reach != 'vpn':
        return said
    hosts = ', '.join(reach.direct) or 'none declared'
    return (
        f'{said} -- this machine reaches {reach.cluster} only over VPN: connect the VPN, or run on a direct host '
        f'({hosts})'
    )
