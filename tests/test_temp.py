"""ONLY OUR OWN IS SWEPT: every shared-temp entry lab-commons creates carries :data:`lab_commons.temp.TEMP_PREFIX`.

The body and its planted control are :mod:`dev_lab.dev.famtests.tempprefix`; this file names lab-commons' facts.
"""

from __future__ import annotations

from pathlib import Path

from dev_lab.dev.famtests import tempprefix

ROOT = Path(__file__).resolve().parents[1]

#: Measured 2026-11-11: 111 ``.py`` files under ``src/``; set below it with room for ordinary deletion.
SRC_FLOOR = 90


def test_every_temp_entry_carries_the_family_prefix() -> None:
    tempprefix.assert_every_temp_entry_carries_the_prefix(base=ROOT, roots=[ROOT / 'src'], floor=SRC_FLOOR)


def test_an_unprefixed_temp_call_is_found(tmp_path: Path) -> None:
    tempprefix.assert_the_scan_finds_a_planted_call(tmp_path=tmp_path)
