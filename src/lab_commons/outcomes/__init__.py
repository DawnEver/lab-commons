"""One task-outcome vocabulary: what happened to each identified task.

Standard library only, and it imports no other ``lab_commons`` subpackage, so every engine can depend
on it without inheriting anything else. Importing ``lab_commons`` does not import it.
"""

from __future__ import annotations

from lab_commons.outcomes.cell import Cell, Cost, Reason
from lab_commons.outcomes.identity import INACTIVE, Identity
from lab_commons.outcomes.status import Evidence, Family, Status

__all__ = ['INACTIVE', 'Cell', 'Cost', 'Evidence', 'Family', 'Identity', 'Reason', 'Status']
