"""The loop: it does not overlap itself, it skips rather than fails, and it stops when asked."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lab_commons.resources import Broker
from lab_commons.supervise.cli import build_registry
from lab_commons.supervise.component import CheckContext, Component, Registry
from lab_commons.supervise.daemon import MIN_INTERVAL, Loop, serve
from lab_commons.supervise.loop import CycleResult
from lab_commons.supervise.process import SystemdProcessManager
from lab_commons.supervise.process.base import Ran
from lab_commons.supervise.state import StateStore
from lab_commons.supervise.verdict import Anomaly, CheckResult, Severity

_CLOCK = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_PROJECT = Path('/srv/target')


class _Probe(Component):
    """Reports nothing, or one anomaly, depending on how it was built."""

    name = 'probe'
    description = 'a probe'

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.runs = 0

    def check(self, ctx: CheckContext) -> CheckResult:
        """Count the call and report."""
        self.runs += 1
        if self.broken:
            return CheckResult(anomalies=[Anomaly(kind='down', severity=Severity.WARNING, message='down')])
        return CheckResult()


def _manager() -> SystemdProcessManager:
    def run(_argv: list[str], _timeout: int, _cwd: str | None) -> Ran:
        return Ran(code=0)

    return SystemdProcessManager(runner=run)


def _loop(tmp_path: Path, probe: Component, *, broker: Broker | None = None) -> Loop:
    registry = Registry()
    registry.register(probe)
    config = {'target': {'name': 'unit-test'}, 'cycle': {'interval': 300, 'check_timeout': 30}, 'alerts': {}}
    return Loop(
        registry=registry,
        store=StateStore(tmp_path / 'state.json'),
        config=config,
        project=_PROJECT,
        manager=_manager(),
        broker=broker,
    )


def test_the_interval_is_floored_so_a_typo_cannot_make_a_busy_loop(tmp_path: Path) -> None:
    """An interval of one second is a supervisor competing with the service it supervises."""
    loop = _loop(tmp_path, _Probe())
    loop.config['cycle']['interval'] = 1
    assert loop.interval() == MIN_INTERVAL


def test_one_cycle_runs_the_checks(tmp_path: Path) -> None:
    """The loop's unit of work is the cycle, and it is the same one the CLI runs."""
    probe = _Probe()
    result = _loop(tmp_path, probe).once(_CLOCK)
    assert result is not None
    assert result.status == 'healthy'
    assert probe.runs == 1


def test_a_cycle_that_finds_the_seat_taken_skips_rather_than_fails(tmp_path: Path) -> None:
    """A refusal is not a fault, and no work is done here either.

    The other cycle is the one doing the work, which is why the seat is taken before measuring.
    """
    broker = Broker()
    probe = _Probe()
    loop = _loop(tmp_path, probe, broker=broker)
    with broker.admit('supervise-unit-test', {'seats': 1}, wait_s=0.0):
        assert loop.once(_CLOCK) is None
    assert probe.runs == 0, 'the seat is taken before any work, so no check should have run'


def test_serve_once_runs_one_cycle_and_returns(tmp_path: Path) -> None:
    """A host driven by a systemd timer wants one cycle per invocation, not a daemon."""
    probe = _Probe()
    seen: list[CycleResult] = []
    code = serve(_loop(tmp_path, probe), once=True, on_cycle=lambda result, _at: seen.append(result))
    assert probe.runs == 1
    assert len(seen) == 1
    assert code == 0


def test_serve_counts_the_cycles_that_found_something_wrong(tmp_path: Path) -> None:
    """The exit status has to carry the answer, because a timer reads nothing else."""
    code = serve(_loop(tmp_path, _Probe(broken=True)), once=True)
    assert code == 1


def test_the_loop_calls_back_once_per_cycle(tmp_path: Path) -> None:
    """The callback is how a journal gets written, and it must fire once per result."""
    seen: list[datetime] = []
    serve(_loop(tmp_path, _Probe()), once=True, on_cycle=lambda _r, at: seen.append(at), clock=lambda: _CLOCK)
    assert seen == [_CLOCK]


def test_a_stop_flag_ends_the_loop_before_it_starts_work(tmp_path: Path) -> None:
    """A signal set between cycles must end the loop rather than run one more."""
    loop = _loop(tmp_path, _Probe())
    loop.stop.set()
    assert list(loop.cycles(lambda: _CLOCK)) == []


def test_build_registry_takes_remedies_and_sections_from_the_config() -> None:
    """A target contributes both through the same file, and neither is smuggled through the other."""
    registry = build_registry(
        {
            'components': {'probe': {'enabled': True}},
            'remedies': {'down': [{'action': 'restart'}]},
        }
    )
    assert registry.remedies('down')[0].action == 'restart'


def test_build_registry_refuses_a_chain_that_names_no_action() -> None:
    """Caught while assembling, not in the middle of applying a remedy."""
    with pytest.raises(ValueError, match='names no action'):
        build_registry({'components': {}, 'remedies': {'down': [{'on': 'critical'}]}})
