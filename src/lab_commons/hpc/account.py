"""WHICH CLUSTER ACCOUNT THIS PROCESS RUNS AS -- read from Slurm and the login, never typed.

A cluster part is claimed in the name of ``<cluster>/<account>`` (:class:`ClusterAccount`): the
Slurm cluster this login node belongs to (``scontrol show config``'s ``ClusterName``) and the Slurm
user the process runs as (``id -un``). Both are facts of the environment, so neither is configured:
a typed account is one that can disagree with the jobs it submits. A box without ``sbatch`` on its
PATH is not on a cluster and has no account (:func:`cluster_account` returns ``None``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

__all__ = ['ClusterAccount', 'cluster_account']

#: Prints the cluster name and the Slurm user, one per line; nothing when this box has no ``sbatch``.
_ASK: Final = (
    "command -v sbatch >/dev/null 2>&1 || exit 0; scontrol show config | sed -n 's/^ClusterName *= *//p'; id -un"
)


@dataclass(frozen=True)
class ClusterAccount:
    """A Slurm cluster and the account a process submits under there."""

    cluster: str
    account: str

    def __str__(self) -> str:
        """``<cluster>/<account>`` -- how a claim names it."""
        return f'{self.cluster}/{self.account}'


def cluster_account(run: Callable[[str, str | None], str]) -> ClusterAccount | None:
    """The account *run* (a shell on this box) submits under, or ``None`` off a cluster or when it cannot say."""
    try:
        said = [line.strip() for line in run(_ASK, None).splitlines() if line.strip()]
    except (OSError, RuntimeError):
        return None
    return ClusterAccount(said[0], said[1]) if len(said) == 2 else None  # noqa: PLR2004 -- two lines asked
