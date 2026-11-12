"""``hpc fetch``: an item's written files come back whole within a budget; nothing escapes ``out``; nothing silent."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from lab_commons.hpc import fetch as door
from lab_commons.hpc.grants import Grant, Machine

_FILES = {'rows_1.jsonl': b'{"a":1}\n', 'logs/structured.jsonl': b'x' * 50, 'big.png': b'p' * 400}


def _cluster(listing: str, files: dict[str, bytes]) -> door.Runner:
    def run(command: str, _stdin: str | None = None) -> str:
        if 'find .' in command:
            return listing
        name = command.rsplit(' -- ', 1)[1].strip("'")
        return base64.b64encode(files[name]).decode()

    return run


def _listing(files: dict[str, bytes]) -> str:
    return ''.join(f'{len(data)}\t{name}\n' for name, data in files.items())


def test_matching_files_land_whole_smallest_first_and_the_rest_are_named_with_their_size(tmp_path: Path) -> None:
    """A 100-byte budget copies the two small files and names the 400-byte one (never a silent omission)."""
    said = door.fetch(_cluster(_listing(_FILES), _FILES), '/o/0', '*', tmp_path, budget=100)
    assert (tmp_path / 'rows_1.jsonl').read_bytes() == _FILES['rows_1.jsonl']
    assert (tmp_path / 'logs' / 'structured.jsonl').read_bytes() == _FILES['logs/structured.jsonl']
    assert not (tmp_path / 'big.png').exists()
    assert said[-1] == 'over budget big.png (400 bytes): raise --budget-mb or narrow --path'


def test_a_match_outside_out_is_refused_by_name_and_no_match_says_so(tmp_path: Path) -> None:
    """The remote marks an escaping symlink; it is named and never copied. An empty match says so (control)."""
    said = door.fetch(_cluster(f'{door.OUTSIDE}\t./link\n', {}), '/o/0', '*', tmp_path, budget=10)
    assert said == ['refused ./link: it resolves outside /o/0']
    assert door.fetch(_cluster('', {}), '/o/0', 'none*', tmp_path, budget=10) == ["no file under /o/0 matches 'none*'"]


@pytest.mark.parametrize('glob', ['../etc/passwd', '/abs/*', 'a/../../b'])
def test_a_path_that_could_leave_out_is_refused_before_any_ssh(glob: str, tmp_path: Path) -> None:
    def never(_command: str, _stdin: str | None = None) -> str:
        pytest.fail('no ssh for a refused path')

    with pytest.raises(ValueError, match='relative to the item out directory'):
        door.fetch(never, '/o/0', glob, tmp_path, budget=10)


def test_the_cli_resolves_the_items_out_and_grant_from_the_job_and_its_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1 lies in the second placement: its grant is reached and its out read; files land beside the job."""
    job = tmp_path / 'j.toml'
    job.write_text('[job]\nentry = "m:f"\nitems = "items.json"\n', encoding='utf-8')
    (tmp_path / 'items.json').write_text(json.dumps([{'out': '/o/0'}, {'out': '/o/1'}]), encoding='utf-8')
    placements = [
        {'items': [0, 1], 'account': ['u0', 'a'], 'targets': ['u0@h0']},
        {'items': [1, 2], 'account': ['u1', 'a'], 'targets': ['u1@h1']},
    ]
    (tmp_path / 'j.submission.json').write_text(json.dumps({'placements': placements}), encoding='utf-8')
    monkeypatch.setattr(door, 'load_grants', lambda: Machine(session=None, grants=()))
    reached: list[Grant] = []
    seen: list[str] = []

    def reach(grant: Grant) -> door.Runner:
        reached.append(grant)

        def run(command: str, _stdin: str | None = None) -> str:
            seen.append(command)
            return ''

        return run

    assert door.main(['-c', str(job), '--item', '1', '--path', 'rows_*'], reach=reach) == 0
    assert [g.user for g in reached] == ['u1']
    assert 'cd /o/1' in seen[0]
    assert door.main(['-c', str(job), '--item', '5', '--path', 'x'], reach=reach) == 2


def test_an_only_submission_maps_the_item_to_its_batch_place_and_binds_its_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--only 4,2``: item 2 ran second, on the second placement, its ``out`` under that run directory."""
    job = tmp_path / 'j.toml'
    job.write_text('[job]\nentry = "m:f"\nitems = "items.json"\n', encoding='utf-8')
    items = [{'out': '{run_dir}/' + str(i)} for i in range(5)]
    (tmp_path / 'items.json').write_text(json.dumps(items), encoding='utf-8')
    placements = [
        {'items': [0, 1], 'account': ['u0', 'a'], 'targets': ['u0@h0'], 'run_dir': '/r/a'},
        {'items': [1, 2], 'account': ['u1', 'a'], 'targets': ['u1@h1'], 'run_dir': '/r/b'},
    ]
    record = {'selected': [4, 2], 'placements': placements}
    (tmp_path / 'j.submission.json').write_text(json.dumps(record), encoding='utf-8')
    monkeypatch.setattr(door, 'load_grants', lambda: Machine(session=None, grants=()))
    reached: list[str] = []
    seen: list[str] = []

    def reach(grant: Grant) -> door.Runner:
        reached.append(grant.user)
        return lambda command, _stdin=None: seen.append(command) or ''

    assert door.main(['-c', str(job), '--item', '2', '--path', 'x'], reach=reach) == 0
    assert reached == ['u1']
    assert 'cd /r/b/2' in seen[0]
    assert door.main(['-c', str(job), '--item', '3', '--path', 'x'], reach=reach) == 2, 'item 3 was not submitted'
