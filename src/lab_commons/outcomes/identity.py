"""What a task IS: a canonical encoding of its subject and context, and the SHA-256 of it.

The encoding is JSON with keys sorted and no whitespace. Floats are written by ``repr`` -- the shortest
string that round-trips exactly -- so ``0.1 + 0.2`` and ``0.3`` are two identities, and ``1`` and
``1.0`` are two identities. Nothing is rounded: rounding would merge two different tasks into one
record. A conditional field that does not apply is given the value :data:`INACTIVE` and is dropped,
so it does not split one task into many; ``None`` is a value and is kept.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = ['INACTIVE', 'Identity']


class _Inactive:
    """The marker type of :data:`INACTIVE`."""

    def __repr__(self) -> str:
        """Spell the marker by its name."""
        return 'INACTIVE'


#: The value of a conditional field that does not apply; it is excluded from the encoding.
INACTIVE: Final = _Inactive()


def _prune(value: object) -> object:
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f'identity key {key!r} is not a string'
                raise TypeError(msg)
            if item is not INACTIVE:
                out[key] = _prune(item)
        return out
    if isinstance(value, list | tuple):
        return [_prune(item) for item in value]
    if value is None or isinstance(value, str | int | float):
        return value
    msg = f'identity value {value!r} has no exact encoding'
    raise TypeError(msg)


def _canonical(value: object) -> str:
    return json.dumps(_prune(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class Identity:
    """A task's canonical encoding and its SHA-256 :attr:`key`. Build with :meth:`of`."""

    canonical: str
    key: str

    @classmethod
    def of(cls, subject: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> Identity:
        """The identity of *subject* under *context*; key order is irrelevant, floats are exact."""
        canonical = _canonical({'subject': subject, 'context': context or {}})
        return cls(canonical, hashlib.sha256(canonical.encode('utf-8')).hexdigest())
