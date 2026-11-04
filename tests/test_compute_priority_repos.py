"""A host may favour a REPOSITORY: its deploy-row name orders that repo's work first on that host."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lab_commons.compute.model import Candidate
from lab_commons.compute.priority import Priority

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _cand(repo: str) -> Candidate:
    return Candidate(repo, 'integrate/main', 'a' * 40, 'linux', 'integration', NOW, 'full')


def test_a_named_repo_matches_by_its_deploy_row_name() -> None:
    favoured = Priority.of({'repos': ['motronics-studio']})
    assert favoured.matches(_cand('Tianjie-Zou-Team/motronics-studio'))
    assert not favoured.matches(_cand('Tianjie-Zou-Team/wdg-lab'))  # PLANTED CONTROL


def test_a_repo_name_is_whole_never_a_suffix() -> None:
    assert not Priority.of({'repos': ['lab']}).matches(_cand('o/wdg-lab'))
    assert not Priority.of({'repos': ['']}).matches(_cand('o/wdg-lab'))


def test_a_repo_declaration_is_carried_and_read() -> None:
    favoured = Priority.of({'repos': ['wdg-lab']})
    assert favoured.row()['repos'] == ['wdg-lab']
    assert favoured.text() == 'repos wdg-lab'


def test_an_unknown_key_is_still_refused() -> None:
    with pytest.raises(ValueError, match='repo'):
        Priority.of({'repo': ['wdg-lab']})
