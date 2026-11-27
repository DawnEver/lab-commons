"""``--set SECTION.KEY=VALUE``: the family's one per-run override grammar, resolved over schema defaults (v1 D125 F5).

Every configurable value is a schema field with a default; a run changes one with ``--set``, never a machine file.
One grammar serves every CLI (ONE-FORMAT-PER-NEED): :func:`add_set_argument` declares the flag, :func:`parse_set`
reads a token, :func:`apply` resolves the tokens over the defaults, and :func:`fragment` is the sparse nested table
a product hands its own loader when it validates there.

THE VALUE IS A TOML LITERAL WHEN IT READS AS ONE -- ``3``, ``1e-9``, ``true``, ``[1, 2]``, ``"x"`` -- ELSE THE TEXT
ITSELF: the shell has already stripped the quotes of ``--set mesh.size="0.5 mm"``, and ``0.5 mm`` is the string a
unit door (:func:`lab_commons.quantity.parse_si`) reads later. Units are never read here.

A PATH MUST NAME A DECLARED KEY: :func:`apply` refuses a key the defaults do not declare, listing the keys that
table does declare, and refuses replacing a whole table by a value. Later tokens win; the defaults are never mutated.
"""

from __future__ import annotations

import argparse
import copy
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = ['SYNTAX', 'Override', 'OverrideRefused', 'add_set_argument', 'apply', 'fragment', 'parse_set']

#: The grammar, as every refusal and the flag's metavar spell it.
SYNTAX: Final = 'SECTION.KEY=VALUE'
_TOKEN: Final = re.compile(r'^(?P<path>[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+)=(?P<value>.*)$', re.DOTALL)


class OverrideRefused(ValueError):
    """A ``--set`` token that is malformed or names no declared key; the message says which and what is settable."""


@dataclass(frozen=True)
class Override:
    """One ``--set``: the dotted *path* of a key and its *value*."""

    path: tuple[str, ...]
    value: Any

    @property
    def dotted(self) -> str:
        """The path as written: ``mesh.order``."""
        return '.'.join(self.path)


def _literal(text: str) -> object:
    try:
        return tomllib.loads(f'v = {text}')['v']
    except tomllib.TOMLDecodeError:
        return text


def parse_set(token: str) -> Override:
    """``'mesh.order=3'`` -> ``Override(('mesh', 'order'), 3)``.

    Raises:
        OverrideRefused: *token* is not ``SECTION.KEY=VALUE`` (at least two dotted identifiers, then ``=``).

    """
    match = _TOKEN.match(token)
    if match is None:
        msg = f'--set {token!r} is not {SYNTAX}: a dotted key of at least two names, "=", then the value'
        raise OverrideRefused(msg)
    return Override(tuple(match['path'].split('.')), _literal(match['value'].strip()))


def fragment(overrides: Iterable[Override]) -> dict[str, Any]:
    """The overrides as one sparse nested table, later tokens winning: ``{'mesh': {'order': 3}}``."""
    out: dict[str, Any] = {}
    for one in overrides:
        table = out
        for name in one.path[:-1]:
            table = table.setdefault(name, {})
        table[one.path[-1]] = one.value
    return out


def apply(defaults: Mapping[str, Any], overrides: Iterable[Override]) -> dict[str, Any]:
    """*defaults* with every override applied in order; *defaults* itself is never changed.

    Raises:
        OverrideRefused: a path names a key the defaults do not declare, or would replace a table by a value.

    """
    resolved = copy.deepcopy(dict(defaults))
    for one in overrides:
        table = resolved
        for depth, name in enumerate(one.path):
            where = '.'.join(one.path[: depth + 1])
            if not isinstance(table, dict) or name not in table:
                declared = ', '.join(sorted(table)) if isinstance(table, dict) else 'no keys'
                owner = f'[{".".join(one.path[:depth])}]' if depth else 'the top level'
                msg = f'--set {where} is not a setting: {owner} declares {declared}'
                raise OverrideRefused(msg)
            if depth == len(one.path) - 1:
                if isinstance(table[name], dict) and not isinstance(one.value, dict):
                    first = min(table[name]) if table[name] else '<key>'
                    msg = f'--set {where} is a table: set one of its keys, such as {where}.{first}=<value>'
                    raise OverrideRefused(msg)
                table[name] = one.value
            else:
                table = table[name]
    return resolved


def _argument(token: str) -> Override:
    try:
        return parse_set(token)
    except OverrideRefused as refused:
        raise argparse.ArgumentTypeError(str(refused)) from refused


def add_set_argument(parser: argparse.ArgumentParser) -> None:
    """Declare ``--set SECTION.KEY=VALUE`` (repeatable; ``args.set`` is the list of :class:`Override`)."""
    parser.add_argument(
        '--set',
        action='append',
        default=[],
        type=_argument,
        metavar=SYNTAX,
        help='override one setting for this run (repeatable; a later --set wins); VALUE is a TOML literal or text',
    )
