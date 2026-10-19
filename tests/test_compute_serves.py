"""WHAT A HOST CLAIMS: ``[serve] parts`` restricts a host to the parts only a resource-carrying host produces."""

from __future__ import annotations

import pytest

from lab_commons.compute.serves import EVERY, NEEDS_A_RESOURCE, Serves


def test_a_restricted_host_skips_the_part_of_no_resource_and_names_the_rule() -> None:
    """PLANTED CONTROL: an unrestricted host skips nothing; a part needing a resource is never skipped."""
    restricted = Serves.of({'parts': NEEDS_A_RESOURCE, 'repos': ['x']})  # the table's other keys are not its
    assert NEEDS_A_RESOURCE in restricted.skips(frozenset())
    assert restricted.skips(frozenset({'jmag'})) == ''
    assert Serves.of({}).skips(frozenset()) == ''
    assert Serves.of({}).parts == EVERY


def test_an_unknown_rule_is_refused_naming_the_rules() -> None:
    with pytest.raises(ValueError, match=NEEDS_A_RESOURCE):
        Serves.of({'parts': 'windows-only'})


def test_a_withheld_resource_skips_every_part_needing_it_and_nothing_else() -> None:
    """``[serve] withhold``: a host keeps a resource for its own workload (a long JMAG run) and claims every other part.

    PLANTED CONTROL: a part not needing the withheld resource is claimed; ``base`` is claimed; an empty withhold
    skips nothing. The rule composes with ``parts``.
    """
    keeps_jmag = Serves.of({'withhold': ['jmag']})
    assert 'jmag' in keeps_jmag.skips(frozenset({'femm', 'jmag'}))
    assert keeps_jmag.skips(frozenset({'femm'})) == ''
    assert keeps_jmag.skips(frozenset()) == ''
    assert Serves.of({'withhold': []}).skips(frozenset({'jmag'})) == ''
    both = Serves.of({'parts': NEEDS_A_RESOURCE, 'withhold': ['jmag']})
    assert both.skips(frozenset())
    assert both.skips(frozenset({'jmag'}))
    assert both.skips(frozenset({'femm'})) == ''
    assert 'withhold' in keeps_jmag.text()


def test_a_malformed_withhold_is_refused_naming_the_key() -> None:
    with pytest.raises(ValueError, match='withhold'):
        Serves.of({'withhold': 'jmag'})
