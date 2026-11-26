"""``python -m lab_commons.tools install|list|check``: the one door through which an external tool reaches a machine.

* ``install <tool> [--version V] [--repo DIR] ...`` -- by the tool's mode in ``tools.toml``:

  - MANAGED (``[--installer FILE] [--mode silent|interactive]``): installs the release of this OS that
    ``--version`` names, else the NEWEST the family floor and the repo's ``[tool.lab_commons.tools]`` admit, under
    the managed root;
    ``--installer`` uses a local copy, hash-verified like a download (:mod:`lab_commons.tools.install`);
  - USER-INSTALLED (``[--home DIR]``): probes the install home this machine declares (or ``--home``, to verify one
    before it is recorded) at the version the repo requires, and never downloads it (:mod:`lab_commons.tools.vendor`).

  ``--repo``: default the nearest directory at or above the current one holding a ``pyproject.toml``. Exit 0
  installed, skipped (already installed and verified) or recorded; 1 refused; 2 usage.
* ``list [--json]`` -- every catalogued tool, its mode, its releases and what this machine has. Exit 0.
* ``check [<tool>...] [--repo DIR]`` -- the lab-commons probe of each tool (default: every catalogued one)
  against the repo's requirement, with its evidence. Exit 0 when every one is present, else 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lab_commons import home
from lab_commons.compute.presence import Requirement, probe
from lab_commons.compute.probe import os_name
from lab_commons.compute.toolchecks import CHECKS
from lab_commons.log import emit
from lab_commons.tools import catalogue, managed, vendor
from lab_commons.tools import install as door


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m lab_commons.tools', description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest='verb', required=True)
    put = verbs.add_parser('install', help='install (managed) or record (user-installed) one tool')
    put.add_argument('tool')
    put.add_argument('--version', help='the version required (default: what the repo declares)')
    put.add_argument('--repo', type=Path, help='the repo whose [tool.lab_commons.tools] states the requirement')
    put.add_argument('--installer', type=Path, help='managed: a local copy of the installer, verified like a download')
    put.add_argument('--mode', choices=door.MODES, help='managed: silent -- progress, no questions (default)')
    put.add_argument('--home', type=Path, help='user-installed: an install home to verify before it is recorded')
    listing = verbs.add_parser('list', help='the catalogued tools and what this machine has')
    listing.add_argument('--json', action='store_true')
    check = verbs.add_parser('check', help='the probe of each tool, with its evidence')
    check.add_argument('tools', nargs='*')
    check.add_argument('--repo', type=Path)
    return parser


def _repo(given: Path | None) -> Path | None:
    """*given*, else the nearest directory at or above the current one holding a ``pyproject.toml``."""
    if given is not None:
        return given
    here = Path.cwd()
    return next((d for d in (here, *here.parents) if (d / 'pyproject.toml').is_file()), None)


def _requirements(given: Path | None) -> tuple[dict[str, Requirement], str]:
    """The repo's declarations inside the family floors of ``tools.toml`` (:func:`catalogue.requirements`)."""
    repo = _repo(given)
    family = f'the family floors in {catalogue.CATALOGUE.name}'
    if repo is None:
        return catalogue.requirements(None, _table()), f'{family}; no repo: no pyproject.toml at or above here'
    return catalogue.requirements(repo, _table()), f'{family} and {repo / "pyproject.toml"}'


def _table() -> dict[str, catalogue.Tool]:
    return catalogue.load(catalogue.CATALOGUE.read_text(encoding='utf-8'))


def _misplaced(args: argparse.Namespace, mode: str) -> list[str]:
    """The flags given that belong to the other mode."""
    other = ('--home',) if mode == catalogue.MANAGED else ('--installer', '--mode')
    return [flag for flag in other if getattr(args, flag.removeprefix('--')) is not None]


def _user_requirement(declared: Requirement | None, version: str | None) -> Requirement | None:
    """The repo's requirement, narrowed to *version* when one is asked; an asked version it refuses is refused."""
    if version is None:
        return declared
    if declared is not None and (why := declared.refusal(version)):
        msg = f'{version} misses the declared requirement {declared.text()} ({declared.reason}): {why}'
        raise LookupError(msg)
    return Requirement(version, 'asked with --version', exact=True)


def _install(args: argparse.Namespace) -> int:
    table = _table()
    if args.tool not in table:
        known = sorted(table)
        emit(
            f'tools install refused: no tool {args.tool!r} in {catalogue.CATALOGUE.name}; catalogued: {known}', err=True
        )
        return 1
    tool = table[args.tool]
    if misplaced := _misplaced(args, tool.mode):
        emit(f'tools install refused: {", ".join(misplaced)} does not apply to {tool.name} ({tool.mode})', err=True)
        return 2
    declared, source = _requirements(args.repo)
    requirement = declared.get(tool.name)
    stated = f'{requirement.text()} ({requirement.reason})' if requirement else 'none declared'
    emit(f'{tool.name} ({tool.mode}); requirement: {stated} -- {source}', flush=True)
    try:
        if tool.mode == catalogue.USER_INSTALLED:
            wanted = _user_requirement(requirement, args.version)
            outcome = vendor.record(tool, wanted, home=args.home, now=datetime.now(UTC))
        else:
            release = catalogue.choose(table, tool.name, os_name(), requirement, version=args.version)
            emit(f'release: {release.version} for {release.system} ({release.kind})', flush=True)
            outcome = door.install(release, mode=args.mode or 'silent', installer=args.installer)
    except LookupError as missing:
        emit(f'tools install refused: {missing}', err=True)
        return 1
    emit(f'{outcome.state}: {outcome.message}', err=outcome.state == 'refused')
    return 1 if outcome.state == 'refused' else 0


def _has(tool: catalogue.Tool) -> dict[str, str] | str:
    """What this machine has of *tool*: the managed record, or the declared home of a user-installed one."""
    if tool.mode == catalogue.USER_INSTALLED:
        declared = vendor.declared_home(tool.vendor)
        return {'home': str(declared.home) if declared.home else 'none', 'declared in': declared.where}
    try:
        one = managed.installed(tool.name)
    except ValueError as unread:
        return f'unreadable record: {unread}'
    return {'version': one.version, 'binary': str(one.binary), 'at': one.at} if one else 'none'


def _list(args: argparse.Namespace) -> int:
    rows = {
        name: {
            'mode': tool.mode,
            'releases': {v: {s: r.kind for s, r in by_os.items()} for v, by_os in tool.releases.items()},
            'here': _has(tool),
        }
        for name, tool in sorted(_table().items())
    }
    if args.json:
        emit(json.dumps({'root': str(home.tools()), 'tools': rows}, indent=1))
        return 0
    emit(f'managed tool root: {home.tools()}')
    for name, row in rows.items():
        emit(f'{name} ({row["mode"]}): {row["here"]}')
        for version, kinds in row['releases'].items():
            emit(f'  {version}: ' + ', '.join(f'{system} ({kind})' for system, kind in kinds.items()))
    return 0


def _check(args: argparse.Namespace) -> int:
    tools = args.tools or sorted(_table())
    if unknown := sorted(set(tools) - set(CHECKS)):
        emit(f'tools check refused: no probe for {unknown}; probed: {sorted(CHECKS)}', err=True)
        return 2
    declared, source = _requirements(args.repo)
    emit(f'requirements: {source}')
    now, absent = datetime.now(UTC), 0
    for tool in tools:
        one = probe(tool, CHECKS[tool], declared.get(tool), now=now)
        absent += not one.present
        emit(f'{tool}: {"present" if one.present else "ABSENT"}')
        for reading in one.evidence:
            emit(f'  {reading.source}: {reading.value}')
    return 1 if absent else 0


def main(argv: list[str] | None = None) -> int:
    """Parse the verb and run it; a malformed catalogue or requirement is refused by name (exit 2)."""
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as usage:
        return 2 if usage.code else 0
    verbs = {'install': _install, 'list': _list, 'check': _check}
    try:
        return verbs[args.verb](args)
    except ValueError as malformed:
        emit(f'tools {args.verb} refused: {malformed}', err=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
