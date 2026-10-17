"""One task-outcome vocabulary: what happened to each identified task.

Standard library only, and it imports no other ``lab_commons`` subpackage, so every engine can depend
on it without inheriting anything else. Importing ``lab_commons`` does not import it.
"""

from __future__ import annotations

from lab_commons.outcomes.status import Evidence, Family, Status

__all__ = ['Evidence', 'Family', 'Status']
