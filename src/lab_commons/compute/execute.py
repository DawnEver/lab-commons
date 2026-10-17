"""THE EXECUTOR -- runs a graph's units; the one local implementation runs each IN-PROCESS (D16, D18, D19).

ONE INTERFACE (:class:`Executor`: ``run(graph) -> {name: Outcome}``), ONE LOCAL IMPLEMENTATION
(:class:`LocalExecutor`). For each unit, in graph order:

1. an upstream that did not produce a result -> the unit is SKIPPED, naming it (a failure stops its dependants);
2. its inputs resolve (a file, or an upstream result's output) and give the unit hash;
3. a stored result for that hash -> CACHED, nothing runs (the store also asks the shared tier);
4. else :data:`PLACEMENT` decides from ``--where`` and whether this box can run it -- its engine and
   environment are PRESENT here and the box ledger admits its demand: RUN here, OFFER it as a Job to a host
   that can (the ``offer`` the caller wires: :func:`lab_commons.dev.forgeunit.offer`), or REFUSE by name;
5. a run calls the unit's entry ``module:function`` as ``entry(inputs, work)`` IN THIS PROCESS (no packing; a
   debugger works) under :func:`lab_commons.box.ledger.hold` at the unit's declared demand; its declared
   outputs in *work* become the result. An exception is a FAILED unit, by name.

INTENT BEFORE ACTION (D19). A run is journaled (``<journal>/<unit hash>.json``) before the entry is called and
the entry's *work* directory is keyed by the unit hash and kept until the result is stored. A process that
dies mid-run therefore leaves the journal entry and the work directory; the next run of the same unit finds
both and RESUMES -- the entry sees its own checkpoint in *work* -- and says so (:attr:`Outcome.resumed`).
"""

from __future__ import annotations

import importlib
import json
import shutil
import socket
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from lab_commons.box.demand import Refused
from lab_commons.box.ledger import hold as box_hold
from lab_commons.compute.graph import Graph
from lab_commons.compute.presence import Presence
from lab_commons.compute.store import Nondeterministic, Record, Result, ResultStore, result_hash
from lab_commons.compute.unit import Engine, Environment, Output, Unit
from lab_commons.dev._forge_journal import durable_write

__all__ = [
    'CACHED',
    'FAILED',
    'OFFERED',
    'PLACEMENT',
    'RAN',
    'REFUSED',
    'SKIPPED',
    'Executor',
    'Hold',
    'LocalExecutor',
    'Offer',
    'Outcome',
    'Where',
    'presence_check',
]

#: An outcome's state.
CACHED: Final = 'cached'
RAN: Final = 'ran'
FAILED: Final = 'failed'
SKIPPED: Final = 'skipped'
OFFERED: Final = 'offered'
REFUSED: Final = 'refused'
#: The states that carry a result a dependant can read.
_PRODUCED: Final = frozenset({CACHED, RAN})

#: What runs the unit here, what offers it elsewhere, what refuses it.
RUN: Final = 'run'
OFFER: Final = 'offer'
REFUSE: Final = 'refuse'


class Where(StrEnum):
    """``--where``: where a unit may run."""

    HERE = 'here'
    ANYWHERE = 'anywhere'
    ELSEWHERE = 'elsewhere'


#: ``--where`` AS DATA: ``(where, this box can run it) -> action``.
PLACEMENT: Final[Mapping[tuple[Where, bool], str]] = {
    (Where.HERE, True): RUN,
    (Where.HERE, False): REFUSE,
    (Where.ANYWHERE, True): RUN,
    (Where.ANYWHERE, False): OFFER,
    (Where.ELSEWHERE, True): OFFER,
    (Where.ELSEWHERE, False): OFFER,
}


@dataclass(frozen=True)
class Outcome:
    """What became of one unit: its state, its result when it has one, why, and the Job when offered."""

    state: str
    unit_hash: str = ''
    result: Result | None = None
    why: str = ''
    job: str = ''
    resumed: bool = False


class Executor(Protocol):
    """Runs a graph."""

    def run(self, graph: Graph) -> dict[str, Outcome]:
        """Every unit's outcome, by name."""


#: The box ledger's ``hold``: ``hold(demand, what, queue_s, *, key=unit hash)``.
Hold = Callable[..., AbstractContextManager[Any]]
#: Offers a unit as a Job: ``offer(unit, unit_hash, files) -> job id``.
Offer = Callable[[Unit, str, Mapping[str, Path]], str]


def presence_check(here: Environment | None, presence: Mapping[str, Presence]) -> Callable[[Engine], str]:
    """Why *engine* cannot run on this host (``''``: it can) -- ours at *here*, a vendor at its probed version."""

    def why(engine: Engine) -> str:
        if engine.environment is not None:
            if engine.environment != here:
                return f'{engine.name} needs environment {engine.environment.key}; this process is {here and here.key}'
            return ''
        try:
            probed = Engine.vendor(engine.name, presence)
        except LookupError as absent:
            return str(absent)
        return (
            '' if probed.version == engine.version else f'{engine.name} {engine.version} wanted, {probed.version} here'
        )

    return why


def _entry(spec: str) -> Callable[[Mapping[str, Any], Path], object]:
    module, _, name = spec.partition(':')
    return getattr(importlib.import_module(module), name)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LocalExecutor:
    """Runs units in this process; see the module docstring."""

    def __init__(
        self,
        store: ResultStore,
        journal: Path,
        *,
        present: Callable[[Engine], str],
        where: Where = Where.HERE,
        offer: Offer | None = None,
        hold: Hold = box_hold,
        queue_s: float = 0.0,
        host: str | None = None,
    ) -> None:
        """Results in *store*; intents in *journal*; *present* says why an engine cannot run here."""
        if offer is None and any(action == OFFER for (w, _), action in PLACEMENT.items() if w is where):
            msg = f'--where {where} may offer a unit, and no offer is wired'
            raise ValueError(msg)
        self.store, self.journal, self.present, self.where = store, journal, present, where
        self.offer, self.hold, self.queue_s = offer, hold, queue_s
        self.host = host or socket.gethostname()

    def pending(self) -> tuple[str, ...]:
        """The unit hashes journaled as running and not yet stored: what a crash left to resume."""
        return tuple(sorted(p.stem for p in self.journal.glob('*.json')))

    def run(self, graph: Graph) -> dict[str, Outcome]:
        """Every unit's :class:`Outcome`, in graph order (the module docstring)."""
        done: dict[str, Outcome] = {}
        for name in graph.order():
            done[name] = self._one(name, graph.units[name], done)
        return done

    def _one(self, name: str, unit: Unit, done: Mapping[str, Outcome]) -> Outcome:
        blocked = sorted(u for u in unit.upstream if done[u].state not in _PRODUCED)
        if blocked:
            return Outcome(SKIPPED, why=f'upstream {", ".join(f"{u} {done[u].state}" for u in blocked)}')
        files = {key: _resolve(value, done) for key, value in unit.inputs.items() if isinstance(value, Path | Output)}
        unit_hash = unit.unit_hash(files)
        held = self.store.get(unit_hash)
        if held is not None:
            return Outcome(CACHED, unit_hash, held)
        absent = self.present(unit.engine)
        action = PLACEMENT[self.where, not absent]
        if action == RUN:
            try:
                return self._run(name, unit, unit_hash, files)
            except Refused as refused:
                absent, action = str(refused), PLACEMENT[self.where, False]
        if action == OFFER and not unit.engine.local_only:
            return Outcome(OFFERED, unit_hash, why=absent, job=self.offer(unit, unit_hash, files))  # type: ignore[misc]
        local = ' (a dirty tree is local-only: it cannot be offered)' if action == OFFER else ''
        return Outcome(REFUSED, unit_hash, why=f'{name}: {absent or "--where " + self.where}{local}')

    def _run(self, name: str, unit: Unit, unit_hash: str, files: Mapping[str, Path]) -> Outcome:
        intent = self.journal / f'{unit_hash}.json'
        work = self.journal / 'work' / unit_hash
        resumed = intent.is_file()
        work.mkdir(parents=True, exist_ok=True)
        started = _now()
        durable_write(intent, json.dumps({'name': name, 'host': self.host, 'started': started}).encode())
        inputs = {key: files.get(key, value) for key, value in unit.inputs.items()}
        with self.hold(unit.demand, f'unit {name}', self.queue_s, key=unit_hash):
            try:
                _entry(unit.entry)(inputs, work)
            except Exception as failed:  # noqa: BLE001 -- a failed unit fails BY NAME, whatever raised
                self._forget(intent, work)
                return Outcome(FAILED, unit_hash, why=f'{name}: {type(failed).__name__}: {failed}')
        missing = [o for o in unit.outputs if not (work / o).exists()]
        if missing:
            self._forget(intent, work)
            return Outcome(FAILED, unit_hash, why=f'{name}: declared outputs not written: {missing}')
        staged = self.store.staging()
        for out in unit.outputs:
            (shutil.copytree if (work / out).is_dir() else shutil.copyfile)(work / out, staged / out)
        engine = unit.engine
        record = Record(
            unit_hash,
            engine.name,
            engine.version,
            engine.environment.key if engine.environment else '',
            self.host,
            started,
            _now(),
            result_hash(staged, unit.outputs),
            engine.local_only,
        )
        try:
            result = self.store.put(staged, record)
        except Nondeterministic as alarm:
            self._forget(intent, work)
            return Outcome(FAILED, unit_hash, why=f'{name}: {alarm}')
        self._forget(intent, work)
        return Outcome(RAN, unit_hash, result, resumed=resumed)

    @staticmethod
    def _forget(intent: Path, work: Path) -> None:
        shutil.rmtree(work, ignore_errors=True)
        intent.unlink(missing_ok=True)


def _resolve(value: Path | Output, done: Mapping[str, Outcome]) -> Path:
    if isinstance(value, Path):
        return value
    result = done[value.unit].result
    if result is None:  # unreachable: an upstream without a result skipped this unit before here
        msg = f'{value.unit} has no result'
        raise RuntimeError(msg)
    return result.output(value.name)
