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
    done = subprocess.run(
        [git, 'config', 'credential.interactive'], capture_output=True, text=True, encoding='utf-8', check=False
    )
    assert done.stdout.strip() == 'never'


def test_the_guarantee_leaves_the_numbered_config_set_to_the_test() -> None:
    """``GIT_CONFIG_COUNT/KEY_n/VALUE_n`` belongs to a test that plants its own set (``git-env-repair``)."""
    numbered = ('GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')
    assert [name for name in os.environ if name.startswith(numbered)] == []
