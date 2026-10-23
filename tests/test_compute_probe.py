"""THE PROBES: a box, its tools and a Slurm account each read into one Supply -- every OS reading faked here."""

from __future__ import annotations

import dataclasses
import math

import pytest
from _box_fake import quiet

from lab_commons.box import GB, Capacity, Holding
from lab_commons.compute.presence import Presence
from lab_commons.compute.probe import account, box, os_name, tools
from lab_commons.hpc.slurm import Held, Node, Quota, Snapshot


def _have(**found: bool) -> dict[str, Presence]:
    """A presence reading: each named tool present or not (how it was read is :mod:`test_compute_presence`'s)."""
    return {tool: Presence(tool, present=present) for tool, present in found.items()}


def test_the_os_is_a_resource_and_an_unknown_one_is_refused() -> None:
    assert [os_name(p) for p in ('win32', 'linux', 'darwin')] == ['windows', 'linux', 'macos']
    with pytest.raises(ValueError, match='sunos'):
        os_name('sunos5')


def test_a_tool_counts_its_seats_less_the_running_jobs_that_use_it() -> None:
    counts, present = tools(
        {'jmag': {'seats': 4}, 'femm': {}},
        running=[frozenset({'jmag'}), frozenset({'jmag', 'windows'}), frozenset()],
        presence=_have(jmag=True, femm=True),
    )
    assert dict(counts) == {'jmag': 2.0, 'femm': 1.0}
    assert present == {'jmag', 'femm'}


def test_a_declared_tool_its_probe_does_not_find_is_absent() -> None:
    counts, present = tools({'jmag': {'seats': 4}}, presence=_have(jmag=False))
    assert present == frozenset()
    assert not counts
    # PLANTED CONTROL: found by the same probe, the same declaration is present.
    assert tools({'jmag': {'seats': 4}}, presence=_have(jmag=True))[1] == {'jmag'}


def test_a_tool_with_every_seat_in_use_is_present_at_zero() -> None:
    counts, present = tools({'femm': {'seats': 1}}, running=[frozenset({'femm'})], presence=_have(femm=True))
    assert present == {'femm'}
    assert counts['femm'] == 0.0


CAPACITY = Capacity(cpu=8.0, mem_bytes=16 * GB, seats={'jmag': 2})
_QUIET = quiet(CAPACITY)


def test_a_box_is_its_measured_room_beside_its_ledger_claims_with_its_os_and_tools() -> None:
    """The box ledger is the one source: what is claimed on it is not free, whoever claimed it.

    On a quiet box (nobody but the claims) the measured room is the capacity less the claims; the measured
    arithmetic itself is pinned in ``test_box_supply.py``.
    """
    held = [Holding('verify:r', 1, cpu=2, mem_bytes=4 * GB, seats={'jmag': 1}, deadline=0.0)]
    supply = box(
        'ws-01', capacity=CAPACITY, read=quiet(CAPACITY), holdings=held, platform='win32', presence=_have(jmag=True)
    )
    assert dict(supply.resources) == {'cpu': 6.0, 'mem_gb': 12.0, 'windows': 6.0, 'jmag': 1.0}
    assert supply.present == {'cpu', 'mem_gb', 'windows', 'jmag'}
    idle = box('ws-01', capacity=CAPACITY, read=_QUIET, platform='win32', presence=_have(jmag=True))
    assert dict(idle.resources) == {'cpu': 8.0, 'mem_gb': 16.0, 'windows': 8.0, 'jmag': 2.0}, 'PLANTED CONTROL'


def test_a_full_box_still_has_its_os_present_and_a_tool_its_probe_misses_is_absent() -> None:
    held = [Holding('producer', 1, cpu=8, mem_bytes=16 * GB, seats={}, deadline=0.0)]
    supply = box(
        'ws-01', capacity=CAPACITY, read=quiet(CAPACITY), holdings=held, platform='linux', presence=_have(jmag=False)
    )
    assert 'linux' in supply.present
    assert 'jmag' not in supply.present
    assert supply.resources['cpu'] == 0.0


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
    supply = account('slurm:me', [(first, 'normal'), (second, 'normal')], seats={})
    assert supply.resources['cpu'] == 164.0  # 100 quota + 64 free on the uncapped grant's nodes: not the first alone
    assert supply.resources['mem_gb'] == pytest.approx(12.0, abs=1e-12)  # GB: 4 quota + 8 free on nodes
    assert math.isinf(supply.resources['submits'])  # the second grant's QOS has no submit cap


def test_an_account_whose_qos_is_full_has_no_submits() -> None:
    supply = account('slurm:me', [(_snapshot(cpus=8, mem_mb=1024, submit=1), 'normal')], seats={})
    assert supply.resources['submits'] == 0.0
    assert 'submits' in supply.present


def test_an_account_supplies_its_declared_tools_through_the_same_probe_as_a_box() -> None:
    """A Slurm account has femm exactly where the probe finds it; the machine file only counts its seats."""
    grant = [(_snapshot(cpus=8, mem_mb=1024, submit=None), 'normal')]
    supply = account('slurm:me', grant, seats={'femm': 1, 'workstation': 1}, presence=_have(femm=True))
    assert supply.resources['femm'] == 1.0
    assert {'femm', 'workstation', 'linux'} <= supply.present
    missing = account('slurm:me', grant, seats={'femm': 1}, presence=_have(femm=False))
    assert 'femm' not in missing.present, 'a declared tool its probe does not find is absent on an account too'
    found = account('slurm:me', grant, seats={}, presence=_have(femm=True))
    assert found.resources['femm'] == 1.0, 'PLANTED CONTROL: found and undeclared is present, at one seat'


def test_a_tool_its_probe_finds_is_present_without_any_declaration() -> None:
    """PRESENCE IS PROBED, NOT DECLARED: no ``[capabilities.<tool>]`` row is needed for a probe-able tool."""
    counts, present = tools({}, presence=_have(simba=True, matlab=True, jmag=False))
    assert present == {'simba', 'matlab'}
    assert dict(counts) == {'simba': 1.0, 'matlab': 1.0}, 'an undeclared tool has one seat'
    assert tools({'matlab': {'seats': 3}}, presence=_have(matlab=True))[0]['matlab'] == 3.0, 'seats declared'


def test_an_account_counts_its_pending_tasks_memory_as_held_like_their_cpus() -> None:
    snapshot = dataclasses.replace(_snapshot(cpus=8, mem_mb=4096, submit=None), pending=(Held(1, 1.0, 1024.0),))
    supply = account('slurm:me', [(snapshot, 'normal')], seats={})
    assert supply.resources['mem_gb'] == pytest.approx(3.0, abs=1e-12), (
        'a pending task is a claim the scheduler honours before a new submission -- its memory as well as its cpus'
    )
