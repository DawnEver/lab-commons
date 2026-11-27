"""A part whose items are not pytest ids: ``RunSpec.entry`` runs each item, ``RunSpec.items`` lists them in the prep.

dev-lab runs cargo on the cluster this way (``dev_lab.dev.cargostep``): one item per workspace x command, each
returning ``{'exit': code, 'node': ...}``. The gather reads each item as a cell named by its node.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from test_hpc_prep import _BUILDING, _login_text
from test_hpc_run import COST, MACHINE, POLICY, SHA, FakeRunCluster

from lab_commons.hpc import pytest_item
from lab_commons.hpc.builds import Builds
from lab_commons.hpc.gather import gather_run
from lab_commons.hpc.prep import prep_dir
from lab_commons.hpc.run import submit_run
from lab_commons.hpc.shell import ENTRY_MODULE, RunSpec, collect_command, parse_entry_items

sys.modules.setdefault('lab_ci_pytest_item', pytest_item)  # the shipped name the entry runner imports
entry_item = importlib.import_module('lab_commons.hpc.entry_item')

PART = 'rust'  # never 'cargo': the login detector reads every command, the run's paths among them
SOURCE = Path(entry_item.__file__)

#: The fake entry module the items and the item runner resolve to; what each node returns, or raises.
_FAKE = 'lab_ci_test_cargo'
_OUTCOME: dict[str, Any] = {
    'cargo fmt a/Cargo.toml': 0,
    'cargo test a/Cargo.toml': 101,
    'cargo clippy a/Cargo.toml': FileNotFoundError('cargo is not on PATH'),
}


@pytest.fixture(autouse=True)
def _cargo_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType(_FAKE)

    def items(tree: Path, sha: str) -> list[dict[str, Any]]:
        assert tree.is_dir()
        assert sha == SHA
        return [{'node': node, 'manifest': 'a/Cargo.toml'} for node in _OUTCOME]

    def run_item(item: dict[str, Any]) -> dict[str, Any]:
        said = _OUTCOME[item['node']]
        if isinstance(said, Exception):
            raise said
        return {'exit': said, 'node': item['node']}

    module.items, module.run_item = items, run_item  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _FAKE, module)


def _spec() -> RunSpec:
    return RunSpec(
        sha=SHA, repo_url='https://g/r.git', part=PART, builds=Builds(install='uv sync --frozen', env_key='k1'),
        entry=f'{_FAKE}:run_item', items=f'{_FAKE}:items',
    )  # fmt: skip


def test_the_entry_runner_is_standalone() -> None:
    """Shipped as text beside the pytest item runner: it imports no lab_commons."""
    assert 'lab_commons' not in SOURCE.read_text(encoding='utf-8').split('"""', 2)[2]


def test_a_part_declares_entry_and_items_together() -> None:
    with pytest.raises(ValueError, match='entry and items'):
        RunSpec(sha=SHA, repo_url='u', entry=f'{_FAKE}:run_item')


def test_the_entry_collector_prints_one_line_per_item(capsys: pytest.CaptureFixture[str], tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)
    assert entry_item.main([f'{_FAKE}:items', SHA]) == 0
    said = parse_entry_items('noise\n' + capsys.readouterr().out)
    assert list(said) == list(_OUTCOME)
    assert said['cargo fmt a/Cargo.toml'] == {'node': 'cargo fmt a/Cargo.toml', 'manifest': 'a/Cargo.toml'}


def test_each_item_is_a_cell_its_exit_decides_and_a_raise_is_an_environment_error(tmp_path: Path) -> None:
    """PLANTED: exit 0 -> ok, exit 101 -> rejected naming the code, a raise -> error naming its cause."""
    payloads = {node: {'node': node} for node in _OUTCOME}
    item = {'ids': list(_OUTCOME), 'stream': str(tmp_path / 'r.jsonl'), 'item': 0, 'entry': f'{_FAKE}:run_item',
            'payloads': payloads}  # fmt: skip
    folded = entry_item.run(item)
    cells = folded['cells']
    assert cells['cargo fmt a/Cargo.toml'] == {'status': 'ok'}
    assert cells['cargo test a/Cargo.toml']['status'] == 'rejected'
    assert 'exit 101' in cells['cargo test a/Cargo.toml']['reason']['text']
    raised = cells['cargo clippy a/Cargo.toml']
    assert raised['status'] == 'error'
    assert raised['reason']['kind'] == 'environment'
    assert 'cargo is not on PATH' in raised['reason']['text']
    assert folded['done']['done'] == 0


def test_the_prep_collects_through_the_entry_items_never_pytest() -> None:
    fake = FakeRunCluster()
    submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    assert f'{ENTRY_MODULE}.py {_FAKE}:items {SHA}' in fake.prep
    assert 'pytest' not in fake.prep.split('"$P/collected.sh"')[1], 'no pytest collection for an entry part'
    sent = _login_text(fake)
    assert not [c for c in sent if any(word in c for word in _BUILDING)], 'the login node runs nothing heavy'
    assert [c for c in sent if 'sbatch ' in c] == [f'cd {prep_dir(SHA, PART)} && sbatch --parsable prep.sbatch']
    assert any(c.endswith(f'/{ENTRY_MODULE}.py') for c in sent), 'the entry runner is shipped as text'


def test_planted_control_the_entry_collection_is_what_the_login_detector_refuses() -> None:
    assert any(word in collect_command(_spec()) for word in _BUILDING)


class _EntryCluster(FakeRunCluster):
    """Answers the entry collection with the items, and a round's streams by RUNNING each item locally."""

    def __init__(self, tmp: Path) -> None:
        super().__init__()
        self.tmp = tmp

    def _answer(self, command: str, stdin: str | None) -> str:
        if f'{ENTRY_MODULE}.py' in command and 'cat >' not in command:
            return ''.join(f'@@@ item {json.dumps({"node": node})}\n' for node in _OUTCOME)
        return super()._answer(command, stdin)

    def _streams(self) -> str:
        stream = self.tmp / f'{len(self.manifests)}.jsonl'
        for item in self.manifests[-1]['items']:
            entry_item.run({**item, 'stream': str(stream)})
        return stream.read_text(encoding='utf-8')


def test_gather_records_each_entry_item_as_a_cell_named_by_its_node(tmp_path: Path) -> None:
    fake = _EntryCluster(tmp_path)
    submit_run(_spec(), MACHINE, lambda _g: fake, cost=COST, policy=POLICY, stamp='t0')
    record = None
    for _ in range(3):
        record = gather_run(SHA, PART, MACHINE, lambda _g: fake, cost=COST, policy=POLICY)
        if record is not None:
            break
    assert record is not None
    assert fake.manifests[0]['entry'] == f'{ENTRY_MODULE}:run'
    assert all(item['entry'] == f'{_FAKE}:run_item' for item in fake.manifests[0]['items'])
    words = {node: cell['status'] for node, cell in record['cells'].items()}
    assert words == {'cargo fmt a/Cargo.toml': 'ok', 'cargo test a/Cargo.toml': 'rejected',
                     'cargo clippy a/Cargo.toml': 'error'}  # fmt: skip
    assert record['handed'] == {}, 'an entry part hands nothing on'
