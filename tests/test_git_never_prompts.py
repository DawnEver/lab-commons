"""Every test runs with git's and Git Credential Manager's prompts off (``tests/conftest.py``)."""

from __future__ import annotations

import os
import shutil
import subprocess

from conftest import ASKPASS, NO_PROMPT


def test_the_env_carries_every_no_prompt_value() -> None:
    assert {name: os.environ.get(name) for name in NO_PROMPT} == NO_PROMPT
    assert [name for name in ASKPASS if name in os.environ] == []


def test_git_reads_credential_interactive_never() -> None:
    git = shutil.which('git')
    assert git is not None
    done = subprocess.run([git, 'config', 'credential.interactive'], capture_output=True, text=True, check=False)
    assert done.stdout.strip() == 'never'
