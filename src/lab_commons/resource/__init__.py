"""ONE RESOURCE PATH: every consumer of every resource is admitted, held, charged and reaped by one engine.

Declared demand -> measured supply (a :class:`Provider` per scope) -> one admission rule -> enforced limit ->
recorded actual. Three kinds (:data:`KINDS`) differ only in their supply formula and their return moment.
"""

from __future__ import annotations

from lab_commons.box.demand import NotAdmitted
from lab_commons.resource.engine import Engine, Held, charged, window_s
from lab_commons.resource.model import KINDS, Actual, Claim, Kind, Provider, kind_of

__all__ = [
    'KINDS', 'Actual', 'Claim', 'Engine', 'Held', 'Kind', 'NotAdmitted', 'Provider', 'charged', 'kind_of', 'window_s',
]  # fmt: skip
