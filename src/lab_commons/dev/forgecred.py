"""The forge credential: how a secret from the git credential store is presented to a forge.

Split out of :mod:`lab_commons.dev.forge` (2026-10-12) when naming WHY a credential is absent pushed
that module past the size band; the store itself -- reading, storing and that diagnosis -- moved on to
the runtime :mod:`lab_commons.credentials` (2026-10-13), which model API keys read too. What stays here
is forge-specific: the header, the refusal statuses and what an exhausted one says.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Final

from lab_commons.credentials import credential_for

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ['AUTH_REFUSED', 'AUTH_TRANSIENT', 'FIRST_STORE', 'authorization', 'token_for']

#: HTTP statuses a forge answers when it does not accept a request's credential RIGHT NOW -- always the
#: SERVER's, never a verdict on the stored credential (user ruling 2026-10-20: the forge never needs a
#: re-login). Every 401/403 measured on these boxes (2026-10-18, 2026-10-20) cleared untouched, including
#: ~30 min in which ``GET /user`` refused the same credential too. So it is retried like any transient.
AUTH_REFUSED: Final = frozenset({401, 403})

#: The one thing an exhausted 401/403 says: the server's, not the credential's; retry later.
AUTH_TRANSIENT: Final = (
    "the forge answered 401/403 -- the server's, not the stored credential's; do not re-store it, retry later"
)

#: Setup guidance where a check cannot tell a box with NO credential from a server refusing one: first-time only.
FIRST_STORE: Final = (
    'a box with no forge credential yet stores one once: `python -m lab_commons.dev.forge auth login`; '
    "one already stored is never re-stored -- a refusal of it is the server's, retry later"
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
