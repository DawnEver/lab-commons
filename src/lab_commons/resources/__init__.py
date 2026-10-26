"""RESOURCES -- every countable thing a consumer asks of a scope, and the ONE path every ask takes.

Declared demand -> measured supply (a :class:`Provider` per scope) -> one admission rule (:func:`admits`) ->
enforced limit -> recorded actual. Three kinds (:data:`KINDS`) differ only in their supply formula and their
return moment (:mod:`lab_commons.resources.engine`). The names and units a Slurm plan sizes by are
:mod:`lab_commons.resources.dimensions`.
"""

from __future__ import annotations

from lab_commons.box.demand import NotAdmitted
from lab_commons.resources.engine import Engine, Held, admits, charged, window_s
from lab_commons.resources.model import KINDS, Actual, Claim, Kind, Provider, kind_of

__all__ = [
    'KINDS', 'Actual', 'Claim', 'Engine', 'Held', 'Kind', 'NotAdmitted', 'Provider', 'admits', 'charged', 'kind_of',
    'window_s',
]  # fmt: skip
