"""``lab_commons.dev.envkey`` — the environment a verdict was earned in, keyed.

The key exists so that a dependency moving underneath a verdict is VISIBLE. So the tests are about
the two ways that could fail to be true: a version that changed without the key changing, and a
distribution that could not be read and was silently dropped -- which hashes identically to a box
that never had it, and would serve a verdict earned without a package to a run that has it.
"""

from typing import ClassVar

import pytest

from lab_commons.dev.envkey import UNREADABLE, env_key, env_manifest, interpreter_identity


class _Dist:
    """A distribution double: ``importlib.metadata`` promises these two attributes and nothing else."""

    def __init__(self, name, version, *, broken=False):
        self.metadata = {} if broken else {'Name': name}
        self.version = None if broken else version


class TestTheManifest:
    def test_the_manifest_is_sorted_so_enumeration_order_cannot_move_the_key(self):
        """``importlib.metadata`` walks ``sys.path`` and promises nothing about the order."""
        order = [_Dist('zeta', '1'), _Dist('alpha', '2'), _Dist('mid', '3')]
        assert env_manifest(order) == env_manifest(list(reversed(order)))
        assert env_manifest(order)[1:] == ('alpha==2', 'mid==3', 'zeta==1')

    def test_the_interpreter_leads_the_manifest(self):
        """Two runs under different Pythons are two environments, and nothing else says so."""
        manifest = env_manifest([_Dist('a', '1')])
        assert manifest[0] == interpreter_identity()
        assert manifest[0].startswith(('cpython-', 'pypy-'))

    def test_a_distribution_that_cannot_be_read_is_recorded_never_dropped(self):
        """Dropped, it hashes exactly like a box that never had the package."""
        manifest = env_manifest([_Dist('readable', '1'), _Dist('', '', broken=True)])
        assert f'{UNREADABLE}=={UNREADABLE}' in manifest
        assert env_manifest([_Dist('readable', '1')]) != manifest

    def test_a_version_that_cannot_be_read_is_not_read_as_no_version(self):
        class _NoVersion:
            metadata: ClassVar[dict] = {'Name': 'halfbroken'}

        assert f'halfbroken=={UNREADABLE}' in env_manifest([_NoVersion()])


class TestTheKey:
    def test_the_key_is_a_function_of_the_lines_and_of_nothing_else(self):
        assert env_key(('cpython-3.12-win32', 'a==1')) == env_key(('cpython-3.12-win32', 'a==1'))
        assert env_key(('cpython-3.12-win32', 'a==1')) != env_key(('cpython-3.12-win32', 'a==2'))

    def test_a_dependency_moving_under_a_verdict_moves_the_key(self):
        before = env_key(env_manifest([_Dist('numpy', '2.0.0')]))
        after = env_key(env_manifest([_Dist('numpy', '2.1.0')]))
        assert before != after

    def test_adding_a_package_moves_the_key(self):
        one = env_key(env_manifest([_Dist('a', '1')]))
        both = env_key(env_manifest([_Dist('a', '1'), _Dist('b', '1')]))
        assert one != both

    def test_an_empty_manifest_is_refused_rather_than_keyed(self):
        """ "I described nothing" and "nothing differs" must not be the same key."""
        with pytest.raises(ValueError, match='names no environment'):
            env_key(())

    def test_a_zero_length_key_is_refused(self):
        with pytest.raises(ValueError, match='floor'):
            env_key(('a==1',), length=0)


class TestAGitInstallIsNamedByItsCommit:
    """The property that kept a second implementation of this module alive in motronics.

    The manifest reads the distribution VERSION and nothing else, so the claim that it can tell
    two checkouts of one declared version apart is a claim about what pip writes there. Planted
    rather than asserted in prose: a docstring saying ``direct_url.json`` is unnecessary is a
    declaration that lies unless something fails when it stops being true.
    """

    def test_two_checkouts_of_ONE_declared_version_key_differently(self):
        """pip writes a VCS install's version as ``<declared>+<sha>``, so the commit is in the line."""
        before = env_key(env_manifest([_Dist('lab-commons', '0.4.0+1a2b3c4')]))
        after = env_key(env_manifest([_Dist('lab-commons', '0.4.0+9f8e7d6')]))
        assert before != after, (
            'two git checkouts of one declared version keyed identically, so a verdict earned on '
            'one commit of a sibling package would be served against another.'
        )

    def test_the_commit_is_LEGIBLE_in_the_manifest_not_only_in_the_hash(self):
        """A reader diffing two manifests must be able to SEE which commit moved."""
        assert 'lab-commons==0.4.0+1a2b3c4' in env_manifest([_Dist('lab-commons', '0.4.0+1a2b3c4')])

    def test_a_plain_release_install_still_keys(self):
        """The other side: a version with no local segment is ordinary, not a degraded reading."""
        assert 'lab-commons==0.4.0' in env_manifest([_Dist('lab-commons', '0.4.0')])
