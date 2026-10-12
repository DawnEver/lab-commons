"""What a fresh account must have before a tick can run on it -- each read free of side effects, each refusal a fix.

Measured 2026-10-12 deploying three accounts on one cluster: every one of these passed the old deploy
(which only ran ``git ls-remote``) and then failed every tick.

* THE FORGE API. A token that reads repositories over git can still be refused by the API, and a tick
  lives on the API. That check is the forge's (``python -m lab_commons.dev.forge auth check``,
  :func:`lab_commons.dev.forgeauth.tick_refusal`); the deploy runs it as a subprocess, because
  :mod:`lab_commons.hpc` imports nothing of :mod:`lab_commons.dev`.
* THE CREDENTIAL STORE (:func:`store_problem`). A tick reads its token through git's credential helper;
  no helper means no token, and a ``store`` file another user can read is a leaked token.
* THE TOOLS (:func:`tool_plan`). ``uv`` always, plus every ``needs`` a served repo declares, looked up on
  the TICK'S PATH (cron's, not the login shell's). ``uv`` and the Rust toolchain come from their
  OFFICIAL installers into ``~/.local/bin`` and ``~/.cargo/bin`` -- user-space, removable, and with no
  PATH modification, because the tick's PATH already names both directories; ``cmake`` comes through
  ``uv tool``. Anything else is the cluster's to provide, so it is refused by name.
* THE GRANT (:func:`grant_of`). A tick on a cluster needs this account's own ``[[hpc.grant]]``
  (:mod:`lab_commons.hpc.grants`): the user from ``id -un``, the account and its CPU limit from
  ``sacctmgr``, this host, and the partitions the deployment declares (a partition list is a choice,
  not a fact Slurm can answer).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

from lab_commons.hpc.deploy_spec import Deployment

__all__ = ['INSTALLERS', 'grant_of', 'store_problem', 'tool_plan']

#: The official, user-space installer of each tool the deploy may install itself, in install order.
INSTALLERS: Final = {
    'uv': 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh',
    'cargo': "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path",
    'rustc': "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path",
    'cmake': 'uv tool install cmake',
}

_PRIVATE: Final = 0o077
_CPU: Final = re.compile(r'(?:^|,)cpu=(\d+)')


def store_problem(helper: str, store: Path, *, mode: int | None) -> str:
    """Why git's credential helper cannot hand a tick its token; ``''`` when it can. *mode* is *store*'s, if any."""
    if not helper:
        return (
            'git has no credential helper, so a tick has no token: '
            '`git config --global credential.helper store`, then `python -m lab_commons.dev.forge auth login`'
        )
    if helper.split(maxsplit=1)[0] != 'store':
        return ''  # another helper keeps its secret elsewhere
    if mode is None:
        return f'{store} is missing: `python -m lab_commons.dev.forge auth login` stores the token there'
    if mode & _PRIVATE:
        return f'{store} is readable by others (mode {mode:o}): `chmod 600 {store}`'
    return ''


def tool_plan(needs: Iterable[str], *, found: Callable[[str], bool]) -> tuple[list[tuple[str, str]], list[str]]:
    """``(installs, refused)``: ``uv`` and each of *needs* not *found*, as ``(tool, installer)`` or refused by name."""
    installs: list[tuple[str, str]] = []
    refused: list[str] = []
    for tool in dict.fromkeys(['uv', *needs]):
        if found(tool):
            continue
        script = INSTALLERS.get(tool)
        if script is None:
            refused.append(tool)
        elif script not in {s for _t, s in installs}:
            installs.append((tool, script))
    return installs, refused


def grant_of(user: str, sacct: str, *, spec: Deployment, host: str) -> dict[str, Any] | str:
    """The account's ``[[hpc.grant]]`` from ``sacctmgr -nP show assoc ... format=account,grptres``; else why not."""
    if not spec.partitions:
        return (
            'declare the partitions its grant may use: --partition <name> (repeatable; partitions = [...] in [deploy])'
        )
    limits: dict[str, str] = {}
    for line in sacct.splitlines():
        account, _, tres = line.strip().partition('|')
        if account:
            limits.setdefault(account, tres)
    if not limits:
        return f'no Slurm association for {user} (sacctmgr show assoc): the cluster has given this account none'
    if spec.slurm_account:
        if spec.slurm_account not in limits:
            return f"[deploy] slurm_account {spec.slurm_account!r} is not one of {user}'s: {', '.join(limits)}"
        account = spec.slurm_account
    elif len(limits) > 1:
        return f'{user} has several Slurm accounts ({", ".join(limits)}): declare one with --slurm-account <account>'
    else:
        (account,) = limits
    cpus = _CPU.search(limits[account])
    if cpus is None:
        return f'Slurm sets {account} no cpu= limit ({limits[account] or "no GrpTRES"}): write [[hpc.grant]] by hand'
    return {
        'user': user,
        'hosts': [host],
        'slurm_account': account,
        'cpus': int(cpus.group(1)),
        'partitions': list(spec.partitions),
    }
