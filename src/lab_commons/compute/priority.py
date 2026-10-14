"""PRIORITY IS ORDERING -- whom a host claims first, declared in its machine file, never a capacity claim.

A serving host's machine file (:mod:`lab_commons.config`) may name the subjects it favours::

    [priority]
    logins = ["YongxuanWu"]   # the forge login a job came from (Job.origin)
    hosts = []                # the host card that created it, vouched for by that login (Job.origin_host)
    sessions = []             # the ref it was pushed to (Job.session)

On THAT host, every candidate matching any subject is ordered before every candidate matching none, in
its claim pass; within each class the score order stands (:func:`lab_commons.compute.model.rank`).
Nothing is subtracted from supply, nothing is reserved, nothing is preempted.

THE GUARANTEE: a priority candidate is the next thing this host claims whenever it can admit it, so its
wait is bounded by the longest running item's wall limit (Slurm ``--time``), not by the queue. A host
declaring nothing keeps pure score order. A job whose subject the forge cannot resolve (an unknown
login is ``''``) matches nothing: priority is never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from lab_commons.compute.model import Candidate

__all__ = ['PRIORITY_TABLE', 'Priority']

#: The machine-file table a host declares its priority in.
PRIORITY_TABLE: Final = 'priority'

_KEYS: Final = ('logins', 'hosts', 'sessions')


@dataclass(frozen=True)
class Priority:
    """The subjects one host orders first: forge logins, host cards, sessions (refs)."""

    logins: frozenset[str] = frozenset()
    hosts: frozenset[str] = frozenset()
    sessions: frozenset[str] = frozenset()

    @classmethod
    def of(cls, table: Mapping[str, Any]) -> Priority:
        """The machine file's ``[priority]`` table; an unknown key is refused by name."""
        unknown = sorted(set(table) - set(_KEYS))
        if unknown:
            msg = f'[{PRIORITY_TABLE}] has unknown key(s) {unknown}; the keys are {list(_KEYS)}'
            raise ValueError(msg)
        return cls(*(frozenset(str(one) for one in table.get(key, ()) if one) for key in _KEYS))

    def matches(self, candidate: Candidate) -> bool:
        """Whether *candidate*'s login, host or session is one this host orders first; ``''`` never matches."""
        return bool(
            (candidate.origin and candidate.origin in self.logins)
            or (candidate.origin_host and candidate.origin_host in self.hosts)
            or (candidate.session and candidate.session in self.sessions)
        )

    def row(self) -> dict[str, list[str]]:
        """As a host card carries it: each subject list, sorted."""
        return {key: sorted(getattr(self, key)) for key in _KEYS}

    def text(self) -> str:
        """How a human reads it: ``logins YongxuanWu; sessions lane/x`` -- ``none`` when nothing is declared."""
        return '; '.join(f'{key} {", ".join(names)}' for key, names in self.row().items() if names) or 'none'
