"""THE PROBES: a box, its tools and a Slurm account each read into one Supply -- every OS reading faked here."""

from __future__ import annotations

import math

import pytest

from lab_commons.compute.probe import (
    account,
    available_mem_gb,
    box,
    busy_fraction,
    free_cores,
    os_name,
    parse_proc_stat,
    tools,
)
from lab_commons.hpc.slurm import Node, Quota, Snapshot
from lab_commons.proc import SystemMemory

PROC_STAT = 'cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 50 0 25 400 25 0 0 0 0 0\n'


def test_proc_stat_counts_iowait_as_idle() -> None:
    assert parse_proc_stat(PROC_STAT) == (850.0, 1000.0)
    with pytest.raises(ValueError, match='aggregate cpu'):
        parse_proc_stat('intr 1 2 3\n')


def test_the_busy_share_is_read_between_two_samples() -> None:
    assert busy_fraction((800.0, 1000.0), (1100.0, 1400.0)) == pytest.approx(0.25, abs=1e-12)  # unitless share
    assert busy_fraction((1.0, 1.0), (1.0, 1.0)) == 0.0  # no time passed: nothing measured busy


def test_free_cores_are_the_count_less_the_busy_ones_floored_at_zero() -> None:
    assert free_cores(cores=8, busy=lambda *_: 2.5) == pytest.approx(5.5, abs=1e-12)  # cores
    assert free_cores(cores=4, busy=lambda *_: 9.0) == 0.0


def test_memory_is_what_the_os_says_is_available_and_unread_is_none() -> None:
    two_gb = SystemMemory(total_bytes=8 * 1024**3, available_bytes=2 * 1024**3)
    assert available_mem_gb(lambda: two_gb) == pytest.approx(2.0, abs=1e-12)  # GB
    assert available_mem_gb(lambda: None) == 0.0


def test_the_os_is_a_resource_and_an_unknown_one_is_refused() -> None:
    assert [os_name(p) for p in ('win32', 'linux', 'darwin')] == ['windows', 'linux', 'macos']
    with pytest.raises(ValueError, match='sunos'):
        os_name('sunos5')


def test_a_tool_counts_its_seats_less_the_running_jobs_that_use_it() -> None:
    counts, present = tools(
        {'jmag': {'seats': 4}, 'femm': {}},
        running=[frozenset({'jmag'}), frozenset({'jmag', 'windows'}), frozenset()],
        probes={'jmag': lambda: True},
    )
    assert dict(counts) == {'jmag': 2.0, 'femm': 1.0}
    assert present == {'jmag', 'femm'}


def test_a_declared_tool_its_probe_does_not_find_is_absent() -> None:
    counts, present = tools({'jmag': {'seats': 4}}, probes={'jmag': lambda: False})
    assert present == frozenset()
    assert not counts
    # PLANTED CONTROL: found by the same probe, the same declaration is present.
    assert tools({'jmag': {'seats': 4}}, probes={'jmag': lambda: True})[1] == {'jmag'}


def test_a_tool_with_every_seat_in_use_is_present_at_zero() -> None:
    counts, present = tools({'femm': {'seats': 1}}, running=[frozenset({'femm'})], probes={})
    assert present == {'femm'}
    assert counts['femm'] == 0.0


def test_a_box_is_its_free_cores_memory_os_and_tools() -> None:
    supply = box(
        'ws-01',
        capabilities={'jmag': {'seats': 2}},
        cores=lambda: 6.0,
        mem_gb=lambda: 12.0,
        platform='win32',
        probes={'jmag': lambda: True},
    )
    assert dict(supply.resources) == {'cpu': 6.0, 'mem_gb': 12.0, 'windows': 6.0, 'jmag': 2.0}
    assert supply.present == {'cpu', 'mem_gb', 'windows', 'jmag'}


def test_a_busy_box_still_has_its_os_present() -> None:
    supply = box('ws-01', cores=lambda: 0.0, mem_gb=lambda: 1.0, platform='linux', probes={})
    assert 'linux' in supply.present
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
    supply = account('slurm:me', [(first, 'normal'), (second, 'normal')])
    assert supply.resources['cpu'] == 164.0  # 100 quota + 64 free on the uncapped grant's nodes: not the first alone
    assert supply.resources['mem_gb'] == pytest.approx(12.0, abs=1e-12)  # GB: 4 quota + 8 free on nodes
    assert math.isinf(supply.resources['submits'])  # the second grant's QOS has no submit cap


def test_an_account_whose_qos_is_full_has_no_submits() -> None:
    supply = account('slurm:me', [(_snapshot(cpus=8, mem_mb=1024, submit=1), 'normal')])
    assert supply.resources['submits'] == 0.0
    assert 'submits' in supply.present
