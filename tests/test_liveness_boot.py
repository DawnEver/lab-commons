"""The boot time is read on every platform this family runs, and a file older than it predates it."""

from __future__ import annotations

import os
import time
from pathlib import Path

from lab_commons import liveness
from lab_commons.liveness import SLACK_S, boot_time, predates_boot


def test_this_box_reports_a_boot_in_the_past() -> None:
    booted = boot_time()
    assert booted is not None, 'every supported platform has a boot-time reading'
    assert 0 < booted < time.time()


def test_a_file_older_than_the_boot_predates_it_and_a_newer_one_does_not(tmp_path: Path, monkeypatch) -> None:
    """PLANTED CONTROL: the same file inside the slack, and an unknown boot, are never debris."""
    record = tmp_path / 'r.json'
    record.write_bytes(b'\0' * 254)
    os.utime(record, (1000.0, 1000.0))
    for booted, expected in ((2000.0, True), (1000.0 + SLACK_S / 2, False), (None, False)):
        monkeypatch.setattr(liveness, 'boot_time', lambda b=booted: b)
        assert predates_boot(record) is expected, booted
    monkeypatch.setattr(liveness, 'boot_time', lambda: 2000.0)
    assert not predates_boot(tmp_path / 'missing')
