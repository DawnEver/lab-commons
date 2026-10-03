"""The three verbs a supervisor is driven by: check once, serve, or summarise a day.

CHECK IS THE ONE THAT RUNS UNDER A TIMER. ``serve`` is for a host that keeps a daemon and lets the
service manager restart it; ``check`` is for a host that would rather use a systemd timer, which
this family already does elsewhere. Both call the same cycle, so a timer and a daemon cannot drift
into running different things -- the defect the predecessor had, where the daemon and the AI loop
each carried their own copy of the remedy chain.

NOTHING IS PRINTED DIRECTLY. Every line goes through :func:`lab_commons.log.emit`, which is the
kit's one sanctioned console writer and the only place that decides an encoding.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from lab_commons.log import emit
from lab_commons.supervise.component import Registry, load_components
from lab_commons.supervise.components import COMPONENTS
from lab_commons.supervise.config import load_config
from lab_commons.supervise.daemon import Loop, serve
from lab_commons.supervise.loop import CycleResult, run_cycle
from lab_commons.supervise.policy import now
from lab_commons.supervise.process import manager_for
from lab_commons.supervise.report import digest, record, summary
from lab_commons.supervise.state import StateStore

__all__ = ['build_registry', 'main']

#: The journal a day's cycle records are appended to, relative to the target.
JOURNAL: Final = 'output/supervise/cycles.jsonl'


def build_registry(config: dict[str, Any]) -> Registry:
    """Assemble the registry a configuration describes.

    The shipped roster is registered first, then the target's. A target component sharing a
    shipped name REPLACES it, and the replacement is explicit rather than a load-order accident.
    Project components are named by ``project.components`` and read from that module's declared
    ``COMPONENTS`` list, so what a target contributes is something it states rather than something
    a directory walk happened to find.

    Args:
        config: the merged configuration.

    Returns:
        The registry, configured and ready.

    """
    registry = Registry()
    for component in COMPONENTS:
        registry.register(component)
    dotted = str(config.get('project', {}).get('components', '') or '')
    if dotted:
        for component in load_components(dotted):
            # A target MAY replace a shipped component by declaring one under the same name. The
            # replacement is explicit -- the name is dropped first -- rather than the silent
            # last-writer-wins the predecessor used, where which probe ran depended on load order.
            if registry.get(component.name) is not None:
                del registry.components[component.name]
            registry.register(component)
    sections = config.get('components', {})
    registry.configure({name: section for name, section in sections.items() if isinstance(section, dict)})
    remedies = config.get('remedies', {})
    registry.set_remedies({kind: chain for kind, chain in remedies.items() if isinstance(chain, list)})
    return registry


def _context(args: argparse.Namespace) -> tuple[dict[str, Any], Registry, StateStore, Path]:
    """Read the configuration, build the registry, and locate the state.

    Args:
        args: the parsed command line.

    Returns:
        The configuration, the registry, the state store and the target directory.

    """
    project = Path(args.project).resolve()
    config = load_config(Path(args.config) if args.config else project / 'config' / 'supervise.toml')
    registry = build_registry(config)
    state_path = Path(str(config.get('state', {}).get('file', '') or project / 'output' / 'supervise' / 'state.json'))
    return config, registry, StateStore(state_path), project


def _journal(project: Path) -> Path:
    """Return the path a day's cycle records are appended to.

    Args:
        project: the target's directory.

    Returns:
        The journal path.

    """
    return project / JOURNAL


def _read_journal(path: Path) -> list[dict[str, Any]]:
    """Read back the records a run of cycles appended.

    An unreadable or half-written line is skipped rather than fatal: the digest is a convenience
    built on a log, and a log that cannot be parsed in full is still mostly true.

    Args:
        path: the journal.

    Returns:
        The records, in the order they were written.

    """
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    """Run one of the supervisor's verbs.

    Args:
        argv: the arguments, or None to read the process's.

    Returns:
        0 on success, 1 when a cycle found something wrong, 2 for a usage error.

    """
    parser = argparse.ArgumentParser(prog='python -m lab_commons.supervise')
    parser.add_argument('verb', choices=('check', 'serve', 'digest'))
    parser.add_argument('--project', default='.', help="the target's directory")
    parser.add_argument('--config', default='', help='the configuration file, when it is not under the target')
    parser.add_argument('--dry-run', action='store_true', help='resolve and gate, but act on nothing')
    args = parser.parse_args(argv)

    config, registry, store, project = _context(args)
    if args.verb == 'digest':
        emit(digest(_read_journal(_journal(project))))
        return 0

    manager = manager_for(str(config.get('process', {}).get('manager', 'systemd')))
    if args.verb == 'check':
        result = run_cycle(registry, store, config, project, manager, clock=now(), dry_run=args.dry_run)
        emit(summary(result))
        return 0 if result.status == 'healthy' else 1

    loop = Loop(registry=registry, store=store, config=config, project=project, manager=manager)
    journal = _journal(project)
    journal.parent.mkdir(parents=True, exist_ok=True)

    def append(result: CycleResult, at: datetime) -> None:
        with journal.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record(result, at)) + '\n')

    degraded = serve(loop, on_cycle=append)
    return 0 if degraded == 0 else 1
