"""What a task IS: a canonical encoding of its subject and context, and the SHA-256 of it.

THE ONE ENCODER OF THE FAMILY (v1 ruling D125 F2): :func:`canonical` is the only canonical JSON a key is computed
over -- an outcomes Identity, a compute unit hash (:meth:`lab_commons.compute.unit.Unit.identity`, so a computed
task's Identity IS its unit hash) and the result store's records all read it.

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

__all__ = ['INACTIVE', 'Identity', 'canonical']


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


def canonical(value: object) -> bytes:
    """*value* as canonical JSON bytes: sorted keys, no whitespace, exact floats, no NaN, :data:`INACTIVE` dropped.

    Raises:
        TypeError: a key that is not a string, or a value with no exact encoding.
        ValueError: a NaN or an infinity.

    """
    text = json.dumps(_prune(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return text.encode('utf-8')


@dataclass(frozen=True)
class Identity:
    """A task's canonical encoding and its SHA-256 :attr:`key`. Build with :meth:`of`."""

    canonical: str
    key: str

    @classmethod
    def of(cls, subject: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> Identity:
        """The identity of *subject* under *context*; key order is irrelevant, floats are exact."""
        return cls.of_value({'subject': subject, 'context': context or {}})

    @classmethod
    def of_value(cls, value: object) -> Identity:
        """The identity of *value* as it stands, encoded by :func:`canonical` -- a unit's facts, for one."""
        encoded = canonical(value)
        return cls(encoded.decode('utf-8'), hashlib.sha256(encoded).hexdigest())
