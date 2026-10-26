"""THE PROVIDERS: a box's OS, its tool seats, a lent count and a Slurm account, each measured into one Supply.

Every OS reading is faked here. A provider measures its scope LESS WHAT IS USED OUTSIDE THE LEDGER; the engine
subtracts what the ledger holds, so what a consumer may take is what lies outside every declared claim.
"""

from __future__ import annotations

import dataclasses
import math

import pytest
from _box_fake import quiet

from lab_commons.box import GB, Capacity, hold
from lab_commons.box.providers import Lent, account_seats, box_engine
from lab_commons.compute.presence import Presence
from lab_commons.compute.probe import os_name
from lab_commons.hpc.provider import SlurmAccount, unmeasured
from lab_commons.hpc.slurm import Held, Node, Quota, Snapshot
from lab_commons.resources import Claim, Engine, admits


def _have(**found: bool) -> dict[str, Presence]:
    """A presence reading: each named tool present or not (how it was read is :mod:`test_compute_presence`'s)."""
    return {tool: Presence(tool, present=present) for tool, present in found.items()}


def test_the_os_is_a_resource_and_an_unknown_one_is_refused() -> None:
    assert [os_name(p) for p in ('win32', 'linux', 'darwin')] == ['windows', 'linux', 'macos']
    with pytest.raises(ValueError, match='sunos'):
        os_name('sunos5')


CAPACITY = Capacity(cpu=8.0, mem_bytes=16 * GB, seats={'jmag': 2})
_QUIET = quiet(CAPACITY)


def _engine(*, platform: str = 'win32', **found: bool) -> Engine:
    return box_engine(capacity=CAPACITY, read=_QUIET, presence=_have(**found), platform=platform)


def test_a_box_is_what_lies_outside_its_ledger_claims_with_its_os_and_tools() -> None:
    """The box ledger is the one source: what is claimed on it is not free, whoever claimed it."""
    idle = _engine(jmag=True).available('box')
    assert dict(idle.resources) == {'cpu': 8.0, 'mem_gb': 16.0, 'windows': 8.0, 'jmag': 2.0}, 'PLANTED CONTROL'
    with hold({'cpu': 2, 'mem_gb': 4, 'jmag': 1}, 60, 'verify:r', 1, key='k', capacity=CAPACITY, read=_QUIET):
        supply = _engine(jmag=True).available('box')
    assert dict(supply.resources) == {'cpu': 6.0, 'mem_gb': 12.0, 'windows': 8.0, 'jmag': 1.0}
    assert supply.present >= {'cpu', 'mem_gb', 'windows', 'jmag'}


def test_a_full_box_still_has_its_os_present_and_a_tool_its_probe_misses_is_absent() -> None:
    with hold({'cpu': 8, 'mem_gb': 16}, 60, 'producer', 1, key='k', capacity=CAPACITY, read=_QUIET):
        supply = _engine(platform='linux', jmag=False).available('box')
    assert 'linux' in supply.present
    assert 'jmag' not in supply.present
    assert supply.resources['cpu'] == 0.0


def test_a_tool_with_every_seat_held_is_present_at_zero() -> None:
    with hold({'jmag': 2}, 60, 'two solves', 1, key='k', capacity=CAPACITY, read=_QUIET):
        supply = _engine(jmag=True).available('box')
    assert 'jmag' in supply.present
    assert supply.resources['jmag'] == 0.0


def test_a_tool_its_probe_finds_is_present_without_any_declaration() -> None:
    """PRESENCE IS PROBED, NOT DECLARED: no ``[capabilities.<tool>]`` row is needed for a probe-able tool."""
    supply = _engine(simba=True, matlab=True, jmag=False).available('box')
    assert {'simba', 'matlab'} <= supply.present
    assert supply.resources['simba'] == 1.0, 'an undeclared tool has one seat'
    assert 'jmag' not in supply.present, 'PLANTED CONTROL: a declared tool its probe misses is absent'


def test_a_lent_count_is_supplied_and_held_like_a_seat() -> None:
    """A JMAG Job System's slots are LENT by their consumer: the engine counts them like any occupancy name."""
    engine = box_engine(capacity=CAPACITY, read=_QUIET, presence={}, lent={'jobslot:ws1': 2})
    assert engine.available('box').resources['jobslot:ws1'] == 2.0
    both = Claim({'jobslot:ws1': 2}, wall_s=60, what='two jobs', key='k')
    assert engine.claimable('box', both), 'PLANTED CONTROL: two free slots admit two jobs'
    with engine.claim('box', both):
        assert engine.available('box').resources['jobslot:ws1'] == 0.0
        assert not engine.claimable('box', Claim({'jobslot:ws1': 1}, wall_s=60, what='third', key='k3'))
    assert Lent({'jobslot:ws1': 2}).names == {'jobslot:ws1'}


def test_the_rule_is_one_door_and_names_what_lacks() -> None:
    verdict = admits({'cpu': 4.0}, {'cpu': 6.0}, running=[{'cpu': 1.0}], what='big')
    assert not verdict
    assert verdict.why == 'lacking cpu (6 asked, 3 free)'
    assert admits({'cpu': 4.0}, {'cpu': 3.0}, running=[{'cpu': 1.0}]), 'PLANTED CONTROL: it fits'
    assert not admits({'cpu': 4.0}, {'cpu': 1.0}, needs={'femm'}), 'a need absent is lacking before any amount'


def test_a_name_no_provider_supplies_is_refused_not_ignored() -> None:
    """``STATED_ONLY`` is deleted: a ``files`` demand on a scope supplying no ``files`` finds 0 of it."""
    assert not admits({'cpu': 4.0, 'mem_gb': 8.0}, {'cpu': 1.0, 'files': 10.0})
    assert admits({'cpu': 4.0, 'files': 100.0}, {'cpu': 1.0, 'files': 10.0}), 'PLANTED CONTROL: supplied, it fits'


def _snapshot(*, cpus: float | None, mem_mb: float | None, submit: int | None, free_mem_mb: float = 0.0) -> Snapshot:
    quota = Quota(
        account='acct', cpus=cpus, mem_mb=mem_mb, gpus=None, default_qos='normal',
        qos_max_submit={'normal': submit}, queued={'normal': 1},
    )  # fmt: skip
    node = Node('n1', ('defq',), 64, free_mem_mb, 0, schedulable=True)
    return Snapshot(nodes=(node,), partitions={}, quota=quota)


def test_an_account_sums_cpu_and_memory_over_every_grant() -> None:
    first = _snapshot(cpus=100, mem_mb=4096, submit=5)
    second = _snapshot(cpus=None, mem_mb=None, submit=None, free_mem_mb=8192)
    supply = SlurmAccount([(first, 'normal'), (second, 'normal')]).measure('slurm:me')
    assert supply.resources['cpu'] == 164.0  # 100 quota + 64 free on the uncapped grant's nodes: not the first alone
    assert supply.resources['mem_gb'] == pytest.approx(12.0, abs=1e-12)  # GB: 4 quota + 8 free on nodes
    assert math.isinf(supply.resources['submits'])  # the second grant's QOS has no submit cap


def test_an_account_whose_qos_is_full_has_no_submits() -> None:
    supply = SlurmAccount([(_snapshot(cpus=8, mem_mb=1024, submit=1), 'normal')]).measure('slurm:me')
    assert supply.resources['submits'] == 0.0
    assert 'submits' in supply.present


def test_an_account_supplies_its_tools_through_the_box_seat_provider() -> None:
    """A Slurm account has femm exactly where the probe finds it, through the ONE seat provider."""
    grant = [(_snapshot(cpus=8, mem_mb=1024, submit=None), 'normal')]
    found = Engine({'slurm:me': [SlurmAccount(grant), account_seats(_have(femm=True))]}).available('slurm:me')
    assert found.resources['femm'] == 1.0, 'found and undeclared is present, at one seat'
    assert {'femm', 'linux'} <= found.present
    missing = Engine({'slurm:me': [SlurmAccount(grant), account_seats(_have(femm=False))]}).available('slurm:me')
    assert 'femm' not in missing.present, 'PLANTED CONTROL: a tool its probe does not find is absent on an account'


def test_an_unmeasured_account_admits_nothing() -> None:
    assert not admits(unmeasured('slurm:me').resources, {'cpu': 1.0, 'mem_gb': 1.0})


def test_an_account_counts_its_pending_tasks_memory_as_held_like_their_cpus() -> None:
    snapshot = dataclasses.replace(_snapshot(cpus=8, mem_mb=4096, submit=None), pending=(Held(1, 1.0, 1024.0),))
    supply = SlurmAccount([(snapshot, 'normal')]).measure('slurm:me')
    assert supply.resources['mem_gb'] == pytest.approx(3.0, abs=1e-12), (
        'a pending task is a claim the scheduler honours before a new submission -- its memory as well as its cpus'
    )
