"""ONE canonical encoding names a task (v1 ruling D125 F2): a computed task's outcomes Identity IS its unit hash.

Before, ``compute.unit`` and ``outcomes.identity`` each spelled their own canonical JSON; two encoders can drift
apart one rule at a time (a non-string key, a NaN) and then one task has two names. The encoder is
:func:`lab_commons.outcomes.identity.canonical`; the unit hash, the store's records and an Identity all read it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lab_commons.compute import store as store_module
from lab_commons.compute import unit as unit_module
from lab_commons.compute.model import Demand
from lab_commons.compute.unit import Engine, Environment, Unit
from lab_commons.outcomes import INACTIVE, Identity, canonical

ENV = Environment('a' * 40, 'k' * 24)


def _unit(params: dict[str, object], mesh: Path) -> Unit:
    return Unit(
        Engine.ours('ours.solve', ENV),
        'pkg.mod:fn',
        {'mesh': mesh, **params},
        ('out.txt',),
        Demand(cpu=1, mem_gb=0.1),
        60,
    )


def test_a_units_identity_is_its_unit_hash(tmp_path: Path) -> None:
    mesh = tmp_path / 'm.msh'
    mesh.write_text('mesh', encoding='utf-8')
    u = _unit({'rpm': 3000.0}, mesh)
    identity = u.identity({'mesh': mesh})
    assert isinstance(identity, Identity)
    assert identity.key == u.unit_hash({'mesh': mesh})
    assert identity.key == hashlib.sha256(identity.canonical.encode('utf-8')).hexdigest()
    # PLANTED CONTROL: a changed parameter moves both, together.
    other = _unit({'rpm': 3001.0}, mesh)
    assert other.identity({'mesh': mesh}).key == other.unit_hash({'mesh': mesh}) != identity.key


def test_the_encoder_is_exact_and_one_spelling_per_value() -> None:
    assert canonical({'b': 1, 'a': [1.0, None]}) == b'{"a":[1.0,null],"b":1}'
    assert canonical({'x': 0.1 + 0.2}) != canonical({'x': 0.3})
    assert canonical({'x': 1}) != canonical({'x': 1.0})
    assert canonical({'x': 1, 'y': INACTIVE}) == canonical({'x': 1})
    with pytest.raises(TypeError, match='key 1 is not a string'):
        canonical({1: 'a'})
    with pytest.raises(ValueError, match='Out of range'):
        canonical({'x': float('nan')})


def test_identity_of_a_subject_is_the_encoding_of_subject_and_context() -> None:
    identity = Identity.of({'case': 'ipm'}, {'mesh': 'fine'})
    assert identity == Identity.of_value({'subject': {'case': 'ipm'}, 'context': {'mesh': 'fine'}})


def test_compute_keeps_no_second_encoder() -> None:
    """No alias (no-backward-compatibility): the encoder has one home."""
    assert 'canonical' not in unit_module.__all__
    assert unit_module.canonical is canonical  # the store's and the unit's encoder is the outcomes one
    assert store_module.canonical is canonical
