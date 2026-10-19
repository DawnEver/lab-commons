"""The git credential store as THE one place a secret lives: read and stored through ``git credential``.

Promoted out of :mod:`lab_commons.dev.forgecred` (2026-10-13) when model API keys joined the forge token
in the same store (user ruling 2026-10-13: Windows and Linux configure keys ONE way, no environment
variables). A runtime caller -- a library reading its provider's key -- has neither the ``dev`` extra nor
a repository, so this module is stdlib only and ``cwd`` is optional. A secret is keyed by the ``https``
host it authenticates against; where it is persisted is the configured helper's decision (Git
Credential Manager, the OS keychain, ``git credential-store`` on a cluster).

``fill`` NEVER PROMPTS: with nothing stored, git's terminal prompt or a GCM window would block a caller
that nobody is watching, so both are switched off in the child whatever the caller's env says, and an
absent secret is refused naming why.

A MODEL API KEY is one more such secret, under its provider's endpoint host (``api.anthropic.com``), and
its username says WHICH key of that host it is: ``api-key`` the default, ``api-key-<task>`` the key ONE
task owns (user ruling 2026-10-19: each task gets its own AI API key). :func:`api_key` reads it; a task key
that is not stored is refused by its username and never falls back to the default -- a task with a
dedicated key must not spend another's. The tasks are declared (:data:`API_KEY_TASKS`) so a misspelt one
is refused rather than stored where nothing reads. ``ai_lab.auth login <provider> [--task T]`` stores them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Final

import lab_commons.launch as _door

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    'API_KEY_TASKS',
    'API_KEY_USERNAME',
    'NoCredential',
    'api_key',
    'credential_for',
    'store_credential',
    'task_username',
]

#: The username of a provider host's DEFAULT model API key.
API_KEY_USERNAME: Final = 'api-key'

#: The tasks that own a dedicated model API key, each stored as ``api-key-<task>``. Never a number:
#: ``api-key-<N>`` is an account's Nth fallback slot (``ai_lab.client``).
API_KEY_TASKS: Final = frozenset(
    {
        'pr-review',  # lab_commons.dev.forgereview, the advisory PR reviewer (Anthropic)
        'optimization',  # the optimization decider driven through ai_lab.connect (motronics turing campaigns)
    }
)

#: Resolved once: a bare name is a PATH lookup at every call site, and a PATH that answers differently
#: between a terminal and a detached launcher describes an environment nobody meant.
_GIT: Final = shutil.which('git') or 'git'

#: Reading a stored credential touches no network; a git that has not answered in half a minute is hung.
_GIT_TIMEOUT_S: Final = 30

#: Overlaid LAST on every child env: a prompt with nobody at the terminal is a hang, never an answer.
#: ``GIT_TERMINAL_PROMPT`` does not stop git's askpass chain (``GIT_ASKPASS``, ``core.askPass``,
#: ``SSH_ASKPASS``); an EMPTY ``GIT_ASKPASS`` does, because git takes the first one SET and runs it only
#: when non-empty. Measured 2026-10-13: VS Code's askpass answered ``github.com`` over an empty store.
_NO_PROMPT: Final = {'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never', 'GIT_ASKPASS': ''}

#: A shell's exit code for a command it could not execute.
_NOT_STARTED: Final = 127

#: What git prints when a configured helper could not be STARTED. Only git's own phrase: its prompt
#: fallback also says "No such file or directory", and that is an empty store, not a missing helper.
_HELPER_NOT_STARTED: Final = ('is not a git command',)


class NoCredential(OSError):
    """The git credential store holds no secret for this host, so there is nothing to authenticate with."""


def credential_for(
    host: str, *, username: str | None = None, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """``(username, secret)`` the git credential store holds for ``https://<host>``; never returned to a log.

    The username travels with the secret because git presents BOTH (HTTP Basic). An EMPTY secret is
    refused rather than returned: a header would carry it, the server would reject it, and a missing
    credential would be reported as the server's problem. ``cwd`` is where git reads its config from
    (a repository's local helper); ``None`` is the caller's current directory. A given *username* selects
    that credential of the host alone: a helper answering with another username is refused, not taken.
    """
    fields_in = {'host': host} | ({'username': username} if username is not None else {})
    done = _credential('fill', fields_in, cwd=cwd, env=env)
    fields = dict(line.partition('=')[::2] for line in done.stdout.splitlines())
    if fields.get('password') and username in {None, fields.get('username')}:
        return fields.get('username', ''), fields['password']
    raise NoCredential(_why_no_credential(host if username is None else f'{username}@{host}', done))


def task_username(task: str | None) -> str:
    """The credential-store username of *task*'s own model API key; ``None`` is the host's default key."""
    if task is None:
        return API_KEY_USERNAME
    if task not in API_KEY_TASKS:
        msg = f'{task!r} is not a declared API-key task; the tasks are {sorted(API_KEY_TASKS)}'
        raise ValueError(msg)
    return f'{API_KEY_USERNAME}-{task}'


def api_key(
    host: str, *, task: str | None = None, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> str:
    """The model API key stored for ``https://<host>``: *task*'s own, else the default; never another's."""
    return credential_for(host, username=task_username(task), cwd=cwd, env=env)[1]


def store_credential(
    host: str, username: str, secret: str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> None:
    """Hand *secret* to the configured git credential helper for ``https://<host>``; nothing touches disk here."""
    done = _credential('approve', {'host': host, 'username': username, 'password': secret}, cwd=cwd, env=env)
    if done.returncode != 0:
        msg = f'git credential approve failed for {host} (exit {done.returncode}); is a credential.helper configured?'
        raise NoCredential(msg)


def _why_no_credential(host: str, done: subprocess.CompletedProcess[str]) -> str:
    """Which of three different failures left *host* without a credential -- each has its own repair.

    Measured 2026-10-12: a given env blanked ``PATH``, so git could not start ``git-credential-manager``
    and the store that HELD the credential was reported as empty. And on Ada a git that could not load
    its own libraries exited 127 under the same message. Neither is a missing credential.
    Reads git's stderr only; the secret travels on stdout and is never part of this text.
    """
    said = ' '.join(done.stderr.split())[:300]
    if done.returncode == _NOT_STARTED:
        return f'git itself could not start (exit {done.returncode}): {said}'
    if any(token in said.lower() for token in _HELPER_NOT_STARTED):
        return f'the credential helper for {host} could not be started (is it on PATH?): {said}'
    return f'no stored credential for {host}: store one in the git credential store first'


def _credential(
    verb: str, fields: Mapping[str, str], *, cwd: Path | None, env: Mapping[str, str] | None
) -> subprocess.CompletedProcess[str]:
    """One ``git credential <verb>`` over https; the secret travels on stdin, never in argv.

    A given env OVERLAYS the host's (git and its helper need ``PATH``, the profile directories and the
    loader path), and the no-prompt switches overlay both.
    """
    payload = ''.join(f'{key}={value}\n' for key, value in {'protocol': 'https', **fields}.items())
    return _door.run(
        [_GIT, 'credential', verb],
        input=payload + '\n',
        cwd=cwd,
        env={**os.environ, **dict(env or {}), **_NO_PROMPT},
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
