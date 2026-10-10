"""THE per-machine configuration: one TOML file, one table per package.

``~/.config/lab-commons/config.toml`` (or the path in ``$LAB_COMMONS_CONFIG``) says what THIS BOX is
and may use -- the facts that differ between workstations sharing one checkout. Each package owns one
top-level table and validates it itself (an unknown key is the package's to refuse); this module only
reads the file and hands a table out. A missing file is an empty configuration: a package that cannot
work without its table refuses by name, as :mod:`lab_commons.hpc.grants` does.

NO SECRETS. Keys, tokens and passwords stay in their own stores (``~/.ssh/config``, the git credential
helper); this file is plain text a user may paste into a bug report.

Example::

    [hpc]
    [[hpc.grant]]
    user = "me"
    hosts = ["login1.cluster", "login2.cluster"]
    slurm_account = "acct-free"
    cpus = 64
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Final

__all__ = ['CONFIG_ENV', 'config_path', 'machine', 'section']

#: Environment override of the per-machine file.
CONFIG_ENV: Final = 'LAB_COMMONS_CONFIG'

#: Files this one replaced: each is refused by name with where its content now lives.
_RETIRED: Final = {'hpc.toml': 'move it under [hpc] in config.toml (keys unchanged except [[grant]] -> [[hpc.grant]])'}


def config_path() -> Path:
    """``$LAB_COMMONS_CONFIG``, else ``~/.config/lab-commons/config.toml``."""
    override = os.environ.get(CONFIG_ENV)
    return Path(override).expanduser() if override else Path.home() / '.config' / 'lab-commons' / 'config.toml'


def machine() -> dict[str, Any]:
    """The whole parsed file; ``{}`` when it does not exist. A retired sibling file is refused by name."""
    path = config_path()
    for name, remedy in _RETIRED.items():
        legacy = path.parent / name
        if legacy.is_file():
            msg = f'{legacy} is retired: {remedy}, then delete it'
            raise ValueError(msg)
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding='utf-8'))


def section(name: str) -> dict[str, Any]:
    """The ``[name]`` table, or ``{}``. The calling package validates its keys."""
    table = machine().get(name, {})
    if not isinstance(table, dict):
        msg = f'{config_path()}: [{name}] must be a table, got {type(table).__name__}'
        raise TypeError(msg)
    return table
