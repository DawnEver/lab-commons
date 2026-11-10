"""THE per-machine configuration: one TOML file, one table per package.

``<machine home>/config/motronics.toml`` (:func:`lab_commons.home.config_path`; or the path in ``$LAB_COMMONS_CONFIG``)
says what THIS BOX is
and may use -- the facts that differ between workstations sharing one checkout. Each package owns one
top-level table and validates it itself (an unknown key is the package's to refuse); this module only
reads the file and hands a table out. A missing file is an empty configuration: a package that cannot
work without its table refuses by name, as :mod:`lab_commons.hpc.grants` does.

ONE TABLE IS EVERY PACKAGE'S: ``[user]``. A fact about the USER or the MACHINE that more than one
package reads -- the text editor, the logging threshold and formats -- lives there once, with ONE
default, read through :func:`user`. Measured 2026-10-13: ``text_editor_command`` and four logging
formats were defined in two consumers' schemas with the same default literal, and the machine file
carried ``text_editor_command`` once per package. So a package table that repeats a key of
:data:`SHARED_KEYS`, at any depth, is REFUSED by :func:`section` with the remedy; there is no
fallback and no alias. The source-side twin is :mod:`dev_lab.dev.famtests.sharedsettings`.

THE FAMILY LAYER. Facts every machine of one family shares (an HPC grant, a spend cap) must not be
hand-copied per machine. The machine file may declare::

    [family]
    source = "/path/to/a/local/checkout"   # a directory, or the family file itself
    file = "family.toml"                   # relative path inside a directory source (the default)
    max_age = "7 d"                        # optional: warn once when the file is older (s, min, h, d)

and :func:`section` then returns the family file's table merged UNDER the machine file's (tables merge
deeply; any other value, an array of tables such as ``[[hpc.grant]]`` included, comes whole from ONE
layer). The source is read LOCALLY: keeping that checkout fresh (a pull of the reviewed config repo) is
the CALLER's duty, never this module's -- ``max_age`` only makes a forgotten pull visible. Refused by
name, never a silent fallback: a declared family file that is absent; a machine file restating a key
the family holds; a family file carrying a table of :data:`LOCAL_ONLY` (one machine's paths).

NO SECRETS. Keys, tokens and passwords stay in their own stores (``~/.ssh/config``, the git credential
helper); this file is plain text a user may paste into a bug report.

Example::

    [hpc]
    [[hpc.grant]]
    user = "me"
    hosts = ["login1.cluster", "login2.cluster"]
    slurm_account = "acct-free"

    [user]
    text_editor_command = "start"
    log_level = "INFO"
"""

from __future__ import annotations

import os
import time
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Final

from lab_commons import home
from lab_commons.log import emit

__all__ = [
    'CONFIG_ENV',
    'FAMILY_TABLE',
    'LOCAL_ONLY',
    'LOG_LEVELS',
    'SHARED_KEYS',
    'USER_TABLE',
    'User',
    'config_path',
    'family',
    'machine',
    'section',
    'user',
]

#: Environment override of the per-machine file.
CONFIG_ENV: Final = 'LAB_COMMONS_CONFIG'

#: Files this one replaced: each is refused by name with where its content now lives.
_RETIRED: Final = {'hpc.toml': 'move it under [hpc] in config.toml (keys unchanged except [[grant]] -> [[hpc.grant]])'}


def config_path() -> Path:
    """``$LAB_COMMONS_CONFIG``, else the machine home's file (:func:`lab_commons.home.config_path`)."""
    override = os.environ.get(CONFIG_ENV)
    return Path(override).expanduser() if override else home.config_path()


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


#: The one table every package shares.
USER_TABLE: Final = 'user'

#: The thresholds :attr:`User.log_level` accepts, upper-cased.
LOG_LEVELS: Final = frozenset({'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'})


@dataclass(frozen=True, kw_only=True)
class User:
    """The ``[user]`` table: settings about the user or the box that every package shares. ONE default each."""

    #: The command that opens a text file.
    text_editor_command: str = 'notepad.exe'
    #: Logging threshold, one of :data:`LOG_LEVELS`; :func:`user` upper-cases the file's spelling.
    log_level: str = 'INFO'
    #: :class:`logging.Formatter` format of a log file; carries file and line.
    log_file_format: str = '%(asctime)s %(levelname)s %(message)s 	location: %(filename)s line%(lineno)d'
    #: :class:`logging.Formatter` format of the console.
    log_console_format: str = '%(message)s %(asctime)s'
    #: ``time.strftime`` date format of both; an invalid one raises inside ``Formatter.formatTime``.
    log_date_format: str = '%m/%d/%Y %H:%M:%S'

    def __post_init__(self) -> None:
        """Refuse a non-string value and a level outside :data:`LOG_LEVELS`."""
        for key, value in asdict(self).items():
            if not isinstance(value, str):
                msg = f'[{USER_TABLE}] {key} must be a string, got {value!r}'
                raise TypeError(msg)
        if self.log_level not in LOG_LEVELS:
            msg = f'[{USER_TABLE}] log_level must be one of {sorted(LOG_LEVELS)}, got {self.log_level!r}'
            raise ValueError(msg)


#: The keys lab-commons owns in :data:`USER_TABLE`; no package table may repeat one.
SHARED_KEYS: Final = frozenset(field.name for field in fields(User))


def _shared_keys_in(value: object, where: str) -> list[str]:
    """Every ``[where.path].key`` naming a :data:`SHARED_KEYS` entry inside *value*, at any depth."""
    if isinstance(value, list):
        return [hit for item in value for hit in _shared_keys_in(item, where)]
    if not isinstance(value, dict):
        return []
    hits = [f'[{where}].{key}' for key in value if key in SHARED_KEYS]
    return hits + [hit for key, inner in value.items() for hit in _shared_keys_in(inner, f'{where}.{key}')]


#: The machine-file table declaring the family layer.
FAMILY_TABLE: Final = 'family'

#: Tables that describe ONE machine; the family layer may not carry them.
LOCAL_ONLY: Final = frozenset({'repos', 'writeroots', 'sync', FAMILY_TABLE})

_SECONDS: Final = {'s': 1, 'min': 60, 'h': 3600, 'd': 86400}

#: Family files already warned stale in this process (warn once).
_stale_warned: set[Path] = set()


def _seconds(text: object) -> float:
    """``"<number> <unit>"`` with a unit of :data:`_SECONDS`, in seconds; anything else is refused."""
    number, _, unit = str(text).strip().partition(' ')
    try:
        return float(number) * _SECONDS[unit.strip()]
    except (KeyError, ValueError):
        units = sorted(_SECONDS)
        msg = f'{config_path()}: [{FAMILY_TABLE}] max_age must be "<number> <unit>", unit in {units}; got {text!r}'
        raise ValueError(msg) from None


def family() -> dict[str, Any]:
    """The parsed family file the machine file declares, or ``{}`` when none is declared."""
    declared = machine().get(FAMILY_TABLE)
    if declared is None:
        return {}
    source = Path(declared['source']).expanduser()
    path = source / declared.get('file', 'family.toml') if source.is_dir() else source
    if not path.is_file():
        msg = (
            f'{path} does not exist, but {config_path()} [{FAMILY_TABLE}] declares it: update the local '
            'checkout of the family config, or fix [family] source/file'
        )
        raise FileNotFoundError(msg)
    if 'max_age' in declared:
        limit = _seconds(declared['max_age'])
        age = time.time() - path.stat().st_mtime
        if age > limit and path not in _stale_warned:
            _stale_warned.add(path)
            emit(
                f'warning: family config {path} is stale ({age / 3600:.1f} h > max_age '
                f'{declared["max_age"]}): update its checkout',
                err=True,
            )
    layer = tomllib.loads(path.read_text(encoding='utf-8'))
    if local := sorted(LOCAL_ONLY & set(layer)):
        msg = f'{path}: [{local[0]}] is local to one machine, never in the family layer: move it to each machine file'
        raise ValueError(msg)
    return layer


def _restated(local: dict[str, Any], shared: dict[str, Any], where: str) -> list[str]:
    """Every ``[where].key`` *local* sets that *shared* already holds (tables compared key by key)."""
    hits: list[str] = []
    for key in local.keys() & shared.keys():
        if isinstance(local[key], dict) and isinstance(shared[key], dict):
            hits += _restated(local[key], shared[key], f'{where}.{key}')
        else:
            hits.append(f'[{where}].{key}')
    return sorted(hits)


def _merged(local: dict[str, Any], shared: dict[str, Any]) -> dict[str, Any]:
    """*shared* under *local*: tables merge deeply; the caller has refused every restated leaf."""
    out = dict(shared)
    for key, value in local.items():
        out[key] = _merged(value, out[key]) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def section(name: str) -> dict[str, Any]:
    """The ``[name]`` table, or ``{}``. The calling package validates its keys.

    The family layer's table (:func:`family`) lies under the machine file's; a key both hold is
    refused. A package table repeating a key :data:`USER_TABLE` owns is refused, naming the remedy.
    """
    table = machine().get(name, {})
    shared = family().get(name, {})
    for where, value in ((config_path(), table), ('the family file', shared)):
        if not isinstance(value, dict):
            msg = f'{where}: [{name}] must be a table, got {type(value).__name__}'
            raise TypeError(msg)
    if hits := _restated(table, shared, name):
        msg = (
            f'{config_path()}: {", ".join(hits)} restates a key the family layer holds: delete it locally, '
            'change it in the family file'
        )
        raise ValueError(msg)
    table = _merged(table, shared)
    if name != USER_TABLE and (hits := _shared_keys_in(table, name)):
        msg = (
            f'{config_path()}: {", ".join(hits)} repeats a setting lab-commons owns: move it to '
            f'[{USER_TABLE}] (read through lab_commons.config.user()) and delete it here'
        )
        raise ValueError(msg)
    return table


def user() -> User:
    """The validated ``[user]`` table; each absent key takes its one default. An unknown key is refused."""
    table = section(USER_TABLE)
    if unknown := sorted(set(table) - SHARED_KEYS):
        msg = f'{config_path()}: [{USER_TABLE}] has no key {unknown[0]!r}; known: {sorted(SHARED_KEYS)}'
        raise ValueError(msg)
    level = table.get('log_level')
    return User(**table) if not isinstance(level, str) else User(**{**table, 'log_level': level.upper()})
