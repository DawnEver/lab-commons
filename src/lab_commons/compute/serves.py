"""WHAT A HOST CLAIMS -- the parts it serves, declared in its machine file as a RULE, never a list of parts.

A serving host's machine file (:mod:`lab_commons.config`) may restrict which test parts it claims::

    [serve]
    parts = "needs-a-resource"   # claim only a part whose need-set names a resource; default "every"

A part IS its need-set (:mod:`lab_commons.hpc.needsets`), so the rule is decided from the part's name alone:
``base`` -- the empty need-set -- is the one part EVERY host can produce, and a host declaring
``needs-a-resource`` leaves it to the hosts that do not (user ruling 2026-10-18: the Windows box produces only
what needs it). "No other platform can produce it" is NOT decidable from the declarations -- a resource names
a tool, never the OS it runs on -- so the rule is the one the need-set does decide. A request is not a part,
so ``parts`` never restricts it; a ``withhold`` does (user ruling 2026-10-28: a withheld resource is never the
host's forge work, part or request -- ``dev_lab.dev.forgetake.skips``). Absent, a host claims every part it admits.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = ['EVERY', 'NEEDS_A_RESOURCE', 'SERVE_TABLE', 'SKIPPED_HERE', 'Serves']

#: The machine-file table a host declares what it serves in.
SERVE_TABLE: Final = 'serve'

#: Claim every part this host admits (the default).
EVERY: Final = 'every'

#: Claim only a part whose need-set names at least one resource: never ``base``.
NEEDS_A_RESOURCE: Final = 'needs-a-resource'

_RULES: Final = {EVERY: 'every part', NEEDS_A_RESOURCE: 'only parts needing a resource'}

#: How a part this host's rule skips is blocked: on its queue row and its host card.
SKIPPED_HERE: Final = 'skipped here'


@dataclass(frozen=True)
class Serves:
    """The parts one host claims, by its declared rule."""

    parts: str = EVERY
    #: Resources this host keeps for its own workload (``[serve] withhold = ["jmag"]``, user ruling 2026-10-19:
    #: the box's JMAG seats run a long campaign, so it claims no part needing JMAG and every other part).
    withholds: frozenset[str] = frozenset()

    @classmethod
    def of(cls, table: Mapping[str, Any]) -> Serves:
        """The ``parts`` and ``withhold`` keys of the machine file's ``[serve]`` table; a bad value is refused."""
        parts = table.get('parts', EVERY)
        if parts not in _RULES:
            msg = f'[{SERVE_TABLE}] parts = {parts!r}; the rules are {list(_RULES)}'
            raise ValueError(msg)
        withhold = table.get('withhold', [])
        if not isinstance(withhold, list) or not all(isinstance(one, str) for one in withhold):
            msg = f'[{SERVE_TABLE}] withhold must be a list of resource names, got {withhold!r}'
            raise ValueError(msg)
        return cls(parts, frozenset(withhold))

    def skips(self, needs: Collection[str]) -> str:
        """Why this host does not claim a part needing *needs*, ``''`` when it claims it."""
        if self.parts == NEEDS_A_RESOURCE and not needs:
            return f'{SKIPPED_HERE}: {self.text()} -- it needs no resource, so any host produces it'
        if kept := sorted(self.withholds.intersection(needs)):
            return (
                f'{SKIPPED_HERE}: {self.text()} -- it needs {", ".join(kept)}, which this host keeps for its own work'
            )
        return ''

    def text(self) -> str:
        """How a human reads it: ``only parts needing a resource (`[serve] parts = "needs-a-resource"`)``."""
        rule = f'{_RULES[self.parts]} (`[{SERVE_TABLE}] parts = "{self.parts}"`)'
        if self.withholds:
            rule += f', none needing {", ".join(sorted(self.withholds))} (`[{SERVE_TABLE}] withhold`)'
        return rule
