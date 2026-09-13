"""``lab_commons.proc`` — the ONE home for reading this box's memory and its process table.

Ported from motronics-studio's ``core/utils/proc.py``. That repo carried TWO duplicated
``_MEMORYSTATUSEX`` ctypes structs -- one in production (``core/utils/proc.py``), one in the dev
tree (``scripts/gate/width.py``) -- because its layering rule forbids the dev tree importing
production code. An installed package is importable from both, so the duplication ends here.

The readings themselves are syscalls and are asserted as PROPERTIES (a total, an available, their
ordering), never as values: a box's free RAM is not a constant. Everything that can be PLANTED --
the descendant walk, the pid-reuse refutation, the unreadable-table case -- is planted, by
injecting the table rather than mocking the function under test.
"""

import os

import pytest

from lab_commons.proc import (
    SystemMemory,
    descendants,
    kill_pid,
    kill_process_tree,
    pid_alive,
    process_tree,
    system_memory,
    working_set_bytes,
)


class TestSystemMemory:
    def test_this_box_is_readable(self):
        # A floor under the scan: finding NOTHING would make every assertion below vacuous.
        reading = system_memory()
        assert reading is not None, 'neither backend could read this box -- the reader is broken'

    def test_available_never_exceeds_total(self):
        reading = system_memory()
        assert 0 < reading.available_bytes <= reading.total_bytes

    def test_used_is_derived_not_read_twice(self):
        reading = SystemMemory(total_bytes=8, available_bytes=3)
        assert reading.used_bytes == 5
        assert reading.used_fraction == pytest.approx(5 / 8)

    def test_reports_bytes_not_gigabytes(self):
        # A pool sizer divides by a per-worker footprint; a value pre-rounded to 0.1 GB has thrown
        # away the only precision that division has.
        assert system_memory().total_bytes > 1024**3


class TestWorkingSet:
    def test_reads_our_own_process(self):
        held = working_set_bytes(os.getpid())
        assert held is not None and held > 0

    def test_unreadable_pid_is_none_not_zero(self):
        # `None` (gone/denied) and `0` are different facts, and only the first may be read as
        # "unknown". A ceiling enforcer must never read `None` as "under the ceiling".
        assert working_set_bytes(-1) is None


class TestLiveness:
    def test_our_own_pid_is_alive(self):
        assert pid_alive(os.getpid()) is True

    def test_a_nonexistent_pid_is_not_alive(self):
        assert pid_alive(-1) is False
        assert pid_alive(0) is False


class TestDescendants:
    """The closure walk, with the process table INJECTED so pid reuse can be planted."""

    def test_collects_the_whole_tree(self):
        rows = {10: ('root.exe', 1), 11: ('mid.exe', 10), 12: ('leaf.exe', 11), 20: ('other.exe', 1)}
        assert descendants(10, rows, lambda _pid: None) == frozenset({10, 11, 12})

    def test_a_pid_absent_from_the_table_has_no_tree(self):
        assert descendants(99, {10: ('root.exe', 1)}, lambda _pid: None) == frozenset()

    def test_refuses_an_edge_whose_child_predates_its_parent(self):
        # The planted landmine: Windows never clears a dead parent's pid off its orphans, so a
        # recycled pid inherits "children" it never spawned. A child cannot predate its parent.
        rows = {10: ('recycled.exe', 1), 11: ('stranger.exe', 10)}
        created = {10: 5000, 11: 1000}
        assert descendants(10, rows, created.get) == frozenset({10})

    def test_keeps_an_edge_with_equal_creation_stamps(self):
        # FILETIME granularity is coarse enough that a fast spawn shares its parent's stamp.
        # Dropping those loses REAL children -- the direction that leaves survivors.
        rows = {10: ('root.exe', 1), 11: ('child.exe', 10)}
        created = {10: 5000, 11: 5000}
        assert descendants(10, rows, created.get) == frozenset({10, 11})

    def test_terminates_on_a_forged_cycle(self):
        rows = {10: ('a.exe', 11), 11: ('b.exe', 10)}
        assert descendants(10, rows, lambda _pid: None) == frozenset({10, 11})


class TestProcessTree:
    def test_unreadable_table_is_empty_never_just_the_root(self, monkeypatch):
        # `frozenset()` and `{pid}` must differ: a caller may not mistake an unreadable machine
        # for a childless process and report having reaped something it never saw.
        monkeypatch.setattr('lab_commons.proc._process_rows', lambda: None)
        assert process_tree(os.getpid()) == frozenset()

    def test_includes_ourselves_on_a_readable_box(self):
        assert os.getpid() in process_tree(os.getpid())


class TestKill:
    def test_killing_a_nonexistent_pid_reports_failure(self):
        # It must be able to FAIL. A cleanup that always reports success is indistinguishable
        # from one that works, from the outside.
        assert kill_pid(-1) is False

    def test_killing_an_unreadable_tree_reaps_nothing(self, monkeypatch):
        monkeypatch.setattr('lab_commons.proc._process_rows', lambda: None)
        assert kill_process_tree(424242) == frozenset()

    def test_kills_descendants_before_the_root(self, monkeypatch):
        # Killing the root first is what leaves survivors: children are orphaned, not reaped,
        # and once orphaned nothing links them back to the tree that spawned them.
        order: list[int] = []
        monkeypatch.setattr('lab_commons.proc.process_tree', lambda _pid: frozenset({10, 11, 12}))
        monkeypatch.setattr('lab_commons.proc.kill_pid', lambda pid: (order.append(pid), True)[1])
        assert kill_process_tree(10) == frozenset({10, 11, 12})
        assert order[-1] == 10, 'the root must be killed LAST'

    def test_reports_only_what_it_confirmed(self, monkeypatch):
        monkeypatch.setattr('lab_commons.proc.process_tree', lambda _pid: frozenset({10, 11}))
        monkeypatch.setattr('lab_commons.proc.kill_pid', lambda pid: pid == 11)
        assert kill_process_tree(10) == frozenset({11})
