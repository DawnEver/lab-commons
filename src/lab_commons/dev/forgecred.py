"""The forge credential: how a secret from the git credential store is presented to a forge.

Split out of :mod:`lab_commons.dev.forge` (2026-10-12) when naming WHY a credential is absent pushed
that module past the size band; the store itself -- reading, storing and that diagnosis -- moved on to
the runtime :mod:`lab_commons.credentials` (2026-10-13), which model API keys read too. What stays here
is forge-specific: the header, the refusal statuses and their remedy.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Final

from lab_commons.credentials import credential_for

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ['AUTH_DOC', 'AUTH_GONE', 'AUTH_REFUSED', 'AUTH_REMEDY', 'AUTH_TRANSIENT', 'authorization', 'token_for']

#: HTTP statuses that MAY refuse the presented credential -- a SUSPICION, never a verdict. One 401 is not
#: evidence the credential is bad: the usual cause is an EXPIRED OAuth access token that git's next ``fill``
#: refreshes (``docs-src/dev/forge.md#credentials``). The verdict is MEASURED: ``GET /user`` is asked, and on
#: its 401 the credential is refreshed as git does (:func:`lab_commons.credentials.refresh_credential`) and
#: the call retried; only a refusal AFTER that refresh names the credential.
AUTH_REFUSED: Final = frozenset({401, 403})

#: Where the one explanation of a forge auth refusal lives; every remedy below points there.
AUTH_DOC: Final = 'docs-src/dev/forge.md#credentials (lab-commons)'

#: The remedy when the forge refused the credential even after git refreshed it: re-storing is NOT the answer.
AUTH_REMEDY: Final = (
    'the forge refused the stored credential even after refreshing it as git does on a 401 -- the account, its '
    f'scopes or the forge itself refuses it; do not re-store it, report this output ({AUTH_DOC})'
)

#: The remedy when the refresh left the store EMPTY (no refresh token, e.g. a plain stored token): store one.
AUTH_GONE: Final = (
    'the forge refused the stored credential and the store holds none after refreshing it as git does -- store '
    f'one: python -m lab_commons.dev.forge auth login ({AUTH_DOC})'
)

#: What a 401/403 says when ``GET /user`` still authenticated the same credential: re-storing cannot help.
AUTH_TRANSIENT: Final = (
    'the forge refused this request, yet the stored credential authenticates (GET /user answered) -- '
    f'do not re-store it; report this output if it persists ({AUTH_DOC})'
)


def authorization(username: str, secret: str, scheme: str) -> str:
    """HTTP Basic with the credential's username, exactly as git presents it; ``<scheme> <secret>`` only without one.

    A secret with no username is a bare token handed to ``auth login`` -- the one case Basic cannot carry.
    """
    if username:
        return 'Basic ' + base64.b64encode(f'{username}:{secret}'.encode()).decode()
    return f'{scheme} {secret}'


def token_for(host: str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> str:
    """The secret half of :func:`lab_commons.credentials.credential_for`."""
    return credential_for(host, cwd=cwd, env=env)[1]
