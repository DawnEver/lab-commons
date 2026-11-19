"""The family's mark on a shared-temp entry: ONE prefix every repo's ``tempfile`` call carries.

A temp root is shared with every program on the machine, so dev-lab's sweep (``dev_lab.dev.tmpsweep``) removes
only what is recognisably ours (user ruling 2026-11-11): an entry named with :data:`TEMP_PREFIX`. A ``mkdtemp`` /
``mkstemp`` / ``TemporaryDirectory`` / ``NamedTemporaryFile`` call that lands in the shared temp dir passes
``prefix=TEMP_PREFIX`` (or an f-string led by it); one with ``dir=`` beside a declared path needs none.

The constant lives HERE, in the stdlib-only base install, because the runtime code of every repo needs it and
lab-commons never imports dev-lab; dev-lab imports it from here. ``dev_lab.dev.famtests.tempprefix`` is the scan.
"""

from typing import Final

__all__ = ['TEMP_PREFIX']

#: The prefix of every shared-temp entry the family's code creates: what marks an entry OURS to sweep.
TEMP_PREFIX: Final = 'dev-lab-'
