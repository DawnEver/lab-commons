"""A host may favour a REPOSITORY: its deploy-row name orders that repo's work first on that host."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from lab_commons.compute.model import Candidate
from lab_commons.compute.priority import PRIORITY_KEYS, Priority

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _cand(repo: str) -> Candidate:
    return Candidate(repo, 'integrate/main', 'a' * 40, 'linux', 'integration', NOW, 'full')


def test_a_named_repo_matches_by_its_deploy_row_name() -> None:
    favoured = Priority.of({'repos': ['motronics-studio']})
    assert favoured.matches(_cand('Tianjie-Zou-Team/motronics-studio'))
    assert not favoured.matches(_cand('Tianjie-Zou-Team/wdg-lab'))  # PLANTED CONTROL


def test_a_repo_name_is_whole_never_a_suffix() -> None:
    assert not Priority.of({'repos': ['lab']}).matches(_cand('o/wdg-lab'))


def test_an_empty_repo_entry_never_matches_a_repo_with_no_name() -> None:
    """The candidate's name IS empty here, so this fails if `of` stops dropping the empty entry.

    PLANTED CONTROL: a Priority holding the empty name directly does match it.
    """
    assert not Priority.of({'repos': ['']}).matches(_cand('o/'))
    assert Priority(repos=frozenset({''})).matches(_cand('o/'))


def test_a_repo_matches_by_name_whatever_its_owner() -> None:
    """The rule is the NAME (the deploy row), never owner/name: one repo lives under different owners per forge."""
    favoured = Priority.of({'repos': ['motronics-studio']})
    assert favoured.matches(_cand('another-owner/motronics-studio'))


def test_the_card_row_carries_every_declared_key() -> None:
    assert list(Priority().row()) == ['logins', 'hosts', 'sessions', 'repos']


def test_a_repo_declaration_is_carried_and_read() -> None:
    favoured = Priority.of({'repos': ['wdg-lab']})
    assert favoured.row()['repos'] == ['wdg-lab']
    assert favoured.text() == 'repos wdg-lab'


def test_an_unknown_key_is_still_refused() -> None:
    with pytest.raises(ValueError, match='repo'):
        Priority.of({'repo': ['wdg-lab']})


def test_a_repo_entry_with_an_owner_is_refused_naming_the_rule() -> None:
    """The rule is the NAME: an owner/name entry would never match, so it is refused, not silently inert."""
    with pytest.raises(ValueError, match='name only'):
        Priority.of({'repos': ['Tianjie-Zou-Team/motronics-studio']})
    assert Priority.of({'repos': ['motronics-studio']}).repos == frozenset({'motronics-studio'})


def test_the_table_keys_are_the_dataclass_fields() -> None:
    assert tuple(field.name for field in dataclasses.fields(Priority)) == PRIORITY_KEYS
    assert tuple(Priority().row()) == PRIORITY_KEYS
