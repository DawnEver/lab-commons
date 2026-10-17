"""A0.3: Identity -- a canonical encoding and its SHA, of a subject and its context."""

from __future__ import annotations

import math

import pytest

from lab_commons.outcomes import INACTIVE, Identity


def test_planted_control_key_order_is_irrelevant() -> None:
    a = Identity.of({'pole': 8, 'slot': 48, 'nested': {'x': 1, 'y': 2}}, {'solver': 'fea', 'mesh': 'fine'})
    b = Identity.of({'nested': {'y': 2, 'x': 1}, 'slot': 48, 'pole': 8}, {'mesh': 'fine', 'solver': 'fea'})
    assert a == b
    assert a.key == b.key


def test_planted_control_floats_are_exact_never_rounded() -> None:
    """``0.1 + 0.2`` is not ``0.3``; an identity that rounded would merge two different designs."""
    assert Identity.of({'x': 0.1 + 0.2}).key != Identity.of({'x': 0.3}).key
    assert Identity.of({'x': 1}).key != Identity.of({'x': 1.0}).key
    assert Identity.of({'x': True}).key != Identity.of({'x': 1}).key


def test_planted_control_inactive_fields_are_excluded() -> None:
    """A conditional field that does not apply is not part of what the task IS."""
    plain = Identity.of({'kind': 'spm', 'poles': 8})
    assert Identity.of({'kind': 'spm', 'poles': 8, 'rib_mm': INACTIVE}) == plain
    assert Identity.of({'kind': 'spm', 'poles': 8, 'sub': {'rib_mm': INACTIVE}}) == Identity.of(
        {'kind': 'spm', 'poles': 8, 'sub': {}}
    )
    assert Identity.of({'kind': 'spm', 'poles': 8, 'rib_mm': None}) != plain, 'None is a value, not inactive'


def test_subject_and_context_are_distinguished() -> None:
    assert Identity.of({'a': 1}, {}) != Identity.of({}, {'a': 1})


def test_the_key_is_the_sha256_of_the_canonical_encoding() -> None:
    identity = Identity.of({'b': [1, 2.5], 'a': 'x'}, {'c': (1,)})
    assert identity.canonical == '{"context":{"c":[1]},"subject":{"a":"x","b":[1,2.5]}}'
    assert len(identity.key) == 64
    assert Identity.of({'b': [1, 2.5], 'a': 'x'}, {'c': [1]}) == identity


def test_what_has_no_exact_encoding_is_refused() -> None:
    for bad in (math.nan, math.inf, object(), {1: 'non-string key'}):
        with pytest.raises((TypeError, ValueError)):
            Identity.of({'x': bad})
