---
name: the-forge-never-needs-a-re-login
description: User ruling 2026-10-20 -- a forge 401/403 is the server's, never the stored credential's; forgework retries it as transient, and no text tells anyone to re-store a credential after a refusal
created: 2026-10-20
---

# The forge never needs a re-login

User ruling 2026-10-20, repeated many times ("永远不需要重新登录forge"): a credential stored once stays
valid. Every 401/403 measured on these boxes (2026-10-18, 2026-10-20) was the SERVER's and cleared with
the credential untouched -- including ~30 min in which `GET /user` answered 401 too. So the 2026-10-18
fix (probe `GET /user`, call a refusal there "the credential") was itself wrong: the probe measured the
same server state, not the credential.

- `forgework.Client._diagnosis`: a 401/403 is `TRANSIENT` -- retried under the normal bound and backoff,
  `EXHAUSTED` when it does not clear. The `GET /user` probe is gone. Other answered 4xx stay `REFUSED`.
- The refused-credential property of `ForgeCallFailed` and its helper are deleted, with every consumer
  branch: the serve tick no longer raises an AUTH alarm on a 401 (AUTH stays for a MISSING credential
  and a missing reviewer key); `forgeschedule.head_login` and `optional` treat an exhausted 401 like any
  failed call (no login / listing skipped). An essential listing still raises as any exhausted call does.
- `forgecred`: the re-store remedy constant is deleted (registered in `hpc/retired.py`); `AUTH_TRANSIENT`
  is the one message for an exhausted 401/403 (the server's, do not re-store, retry later).
  `FIRST_STORE` is the one setup text for checks that cannot tell a box with NO credential from a refused
  one (workstation, hpc deploy, `auth check`): first-time only, never a re-store.
- `tests/test_dev_forgework.py` scans every string under `src/` for a refusal paired with a re-login,
  with a planted control.
