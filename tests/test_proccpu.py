"""``lab_commons.proccpu`` -- cpu-seconds of a process and of its tree, read from the kernel.

The reading itself is a syscall and is asserted as a PROPERTY (it grows while we compute); the tree
bookkeeping -- an exited child, a recycled pid, nothing readable -- is PLANTED through its seams.
"""

import os

import pytest

from lab_commons.proccpu import TreeCpu, cpu_seconds


class TestCpu:
    def test_our_own_cpu_is_read_and_grows(self) -> None:
        before = cpu_seconds(os.getpid())
        if before is None:
            pytest.xfail('no cpu backend on this platform')
        sum(i * i for i in range(2_000_000))
        after = cpu_seconds(os.getpid())
        assert after is not None
        assert after > before

    def test_unreadable_pid_is_none_not_zero(self) -> None:
        assert cpu_seconds(-1) is None

    def test_a_child_that_exited_keeps_the_cpu_it_was_last_seen_using(self) -> None:
        tables = iter([{1: (100, 1.0), 2: (101, 3.0)}, {1: (100, 2.0)}])
        table: dict[int, tuple[int, float]] = {}

        def tree(_pid: int) -> frozenset[int]:
            table.clear()
            table.update(next(tables))
            return frozenset(table)

        meter = TreeCpu(1, tree=tree, times=table.get)
        assert meter.sample() == 4.0
        assert meter.sample() == 5.0, 'the exited child still counts its 3 s; the root grew to 2 s'

    def test_a_reused_pid_is_a_new_process_not_a_regression(self) -> None:
        readings = iter([(100, 5.0), (200, 1.0)])
        meter = TreeCpu(1, tree=lambda _pid: frozenset({1}), times=lambda _pid: next(readings))
        meter.sample()
        assert meter.sample() == 6.0

    def test_nothing_readable_is_none_never_zero(self) -> None:
        meter = TreeCpu(1, tree=lambda _pid: frozenset({1}), times=lambda _pid: None)
        assert meter.sample() is None
