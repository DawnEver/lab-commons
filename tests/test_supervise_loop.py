"""One cycle end to end: measure, act, say -- and the three ways it refuses to do any of them."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from lab_commons.resources import Broker, Exhausted
from lab_commons.supervise.alert import Notifier
from lab_commons.supervise.component import ActionContext, CheckContext, Component, Handler, Registry
from lab_commons.supervise.loop import CycleResult, run_cycle
from lab_commons.supervise.process import SystemdProcessManager
from lab_commons.supervise.process.base import Ran
from lab_commons.supervise.state import StateStore
from lab_commons.supervise.verdict import Action, Anomaly, CheckResult, Condition, RemedyStep, Severity

_CLOCK = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_PROJECT = Path('/srv/target')


class _Probe(Component):
    """Reports whatever it was constructed with."""

    name = 'probe'
    description = 'a probe'
    anomalies: ClassVar[list[Anomaly]] = []
    metrics: ClassVar[dict[str, float]] = {}

    def check(self, ctx: CheckContext) -> CheckResult:
        """Return the canned result."""
        return CheckResult(metrics=dict(self.metrics), anomalies=list(self.anomalies))


class _Broken(Component):
    """Raises instead of reporting, which is a component a cycle must survive."""

    name = 'broken'
    description = 'a component that raises'

    def check(self, ctx: CheckContext) -> CheckResult:
        """Fail."""
        message = 'the probe itself is broken'
        raise RuntimeError(message)


class _Hanging(Component):
    """Never answers."""

    name = 'hanging'
    description = 'a component that hangs'

    def check(self, ctx: CheckContext) -> CheckResult:
        """Sleep past any reasonable wall."""
        time.sleep(5)
        return CheckResult()


class _Deployer(Component):
    """Declares one action it implements, and one chain reaching it."""

    name = 'deployer'
    description = 'a component that acts'

    def check(self, ctx: CheckContext) -> CheckResult:
        """Report one anomaly for the chain to act on."""
        return CheckResult(anomalies=[Anomaly(kind='stale', severity=Severity.CRITICAL, message='stale build')])

    def actions(self) -> dict[str, Action]:
        """Declare an implemented action and a command action."""
        return {
            'refresh': Action(description='implemented'),
            'shout': Action(description='a command', command='echo hi'),
        }

    def handlers(self) -> dict[str, Handler]:
        """Implement one of them."""

        def refresh(_ctx: ActionContext) -> bool:
            _Deployer.calls.append('refresh')
            return True

        return {'refresh': refresh}

    def remedies(self) -> dict[str, list[RemedyStep]]:
        """Reach the implemented action for the anomaly this component reports."""
        return {'stale': [RemedyStep(action='refresh')]}

    calls: ClassVar[list[str]] = []


class _Reply:
    """A canned HTTP reply."""

    status = 200

    def read(self) -> bytes:
        """Return the body."""
        return b'ok'


class _Connection:
    """Records nothing and answers 200."""

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        """Accept the request, and do nothing with it."""

    def getresponse(self) -> _Reply:
        """Return the canned reply."""
        return _Reply()

    def close(self) -> None:
        """Close, which for a fake is nothing at all."""


class _Transport:
    """A transport that remembers what it was asked for."""

    def __init__(self) -> None:
        self.hosts: list[tuple[str, float]] = []
        self.schemes: list[str] = []

    def __call__(self, scheme: str, host: str, timeout: float) -> _Connection:
        self.schemes.append(scheme)
        self.hosts.append((host, timeout))
        return _Connection()


def _manager(seen: list[tuple[list[str], int, str | None]]) -> SystemdProcessManager:
    def run(argv: list[str], timeout: int, cwd: str | None) -> Ran:
        seen.append((argv, timeout, cwd))
        return Ran(code=0, out='done')

    return SystemdProcessManager(runner=run)


def _registry(*components: Component) -> Registry:
    registry = Registry()
    for component in components:
        registry.register(component)
    return registry


def _config(**cycle: object) -> dict[str, Any]:
    base: dict[str, Any] = {'interval': 300, 'check_timeout': 30}
    base.update(cycle)
    return {'target': {'name': 'unit-test'}, 'cycle': base, 'alerts': {}, 'components': {}}


@pytest.fixture(autouse=True)
def _clean_components() -> None:
    """Each test sees only the canned state it set, not the previous test's."""
    _Probe.anomalies = []
    _Probe.metrics = {}
    _Deployer.calls = []


def _run(registry: Registry, tmp_path: Path, config: dict[str, Any] | None = None, **kwargs: object) -> CycleResult:
    manager = kwargs.pop('manager', _manager([]))
    return run_cycle(
        registry,
        StateStore(tmp_path / 'state.json'),
        config or _config(),
        _PROJECT,
        manager,
        clock=_CLOCK,
        **kwargs,
    )


def test_a_quiet_cycle_is_healthy_and_records_when(tmp_path: Path) -> None:
    """The clean case: nothing wrong, counters dropped, and the moment written down."""
    result = _run(_registry(_Probe()), tmp_path)
    assert result.status == 'healthy'
    assert result.anomalies == []
    assert result.state['last_quiet'] == _CLOCK.isoformat()
    assert (tmp_path / 'state.json').is_file()


def test_a_quiet_cycle_clears_a_counter_a_previous_one_raised(tmp_path: Path) -> None:
    """A condition that cleared must stop counting, or escalation fires on a healthy target."""
    store = StateStore(tmp_path / 'state.json')
    _Probe.anomalies = [Anomaly(kind='bad', severity=Severity.WARNING, message='bad')]
    run_cycle(_registry(_Probe()), store, _config(), _PROJECT, _manager([]), clock=_CLOCK)
    assert store.read()['anomalies'] != {}
    _Probe.anomalies = []
    run_cycle(_registry(_Probe()), store, _config(), _PROJECT, _manager([]), clock=_CLOCK)
    assert store.read()['anomalies'] == {}


def test_an_anomaly_is_stamped_with_its_component(tmp_path: Path) -> None:
    """The stamped source is the key every other rule is keyed by."""
    _Probe.anomalies = [Anomaly(kind='bad', severity=Severity.WARNING, message='bad')]
    result = _run(_registry(_Probe()), tmp_path)
    assert result.status == 'degraded'
    assert result.anomalies[0].source == 'probe.bad'


def test_a_component_that_raises_becomes_a_critical_anomaly(tmp_path: Path) -> None:
    """A probe that cannot run is worse than one reporting a problem: nothing is watching."""
    result = _run(_registry(_Broken()), tmp_path)
    assert result.status == 'degraded'
    assert result.anomalies[0].kind == 'check_failed'
    assert result.anomalies[0].severity is Severity.CRITICAL
    assert 'RuntimeError' in result.anomalies[0].message


def test_a_component_that_never_answers_is_bounded_by_the_wall(tmp_path: Path) -> None:
    """THE BUG THIS TEST FOUND: a `with` pool joins its thread on exit, so the wall bounded nothing."""
    started = time.monotonic()
    result = _run(_registry(_Hanging()), tmp_path, config=_config(check_timeout=1))
    elapsed = time.monotonic() - started
    assert result.anomalies[0].kind == 'check_failed'
    assert 'did not answer' in result.anomalies[0].message
    assert elapsed < 4, f'the cycle waited {elapsed:.1f}s for a check it had already given up on'


def test_an_implemented_action_runs(tmp_path: Path) -> None:
    """A component that acts in Python says so, and the chain reaches it."""
    result = _run(_registry(_Deployer()), tmp_path)
    assert _Deployer.calls == ['refresh']
    assert [attempt.action for attempt in result.attempts] == ['refresh']
    assert result.attempts[0].ok is True


def test_a_command_action_runs_under_a_ceiling(tmp_path: Path) -> None:
    """Every remedy command is capped, so a reinstall cannot take the host down while we watch."""
    seen: list[tuple[list[str], int, str | None]] = []

    class _Cmd(_Deployer):
        def remedies(self) -> dict[str, list[RemedyStep]]:
            """Reach the command action instead."""
            return {'stale': [RemedyStep(action='shout')]}

    result = _run(_registry(_Cmd()), tmp_path, manager=_manager(seen))
    assert result.attempts[0].ok is True
    argv = seen[0][0]
    assert argv[:2] == ['systemd-run', '--scope']
    assert '--property=MemoryMax=400M' in argv
    assert argv[-3:] == ['sh', '-c', 'echo hi']


def test_a_step_gated_off_is_recorded_as_skipped_not_failed(tmp_path: Path) -> None:
    """A report must tell 'we chose not to' from 'we tried and it did not work'."""

    class _Gated(_Deployer):
        def remedies(self) -> dict[str, list[RemedyStep]]:
            """Gate the only step on a severity this anomaly does not have."""
            return {'stale': [RemedyStep(action='refresh', on='warning')]}

    result = _run(_registry(_Gated()), tmp_path)
    assert result.attempts[0].skipped is True
    assert result.attempts[0].ran is False
    assert _Deployer.calls == []


def test_a_step_whose_condition_is_not_met_is_skipped(tmp_path: Path) -> None:
    """The gate is data, and a condition that does not hold means the step does not run."""

    class _Conditional(_Deployer):
        def remedies(self) -> dict[str, list[RemedyStep]]:
            """Gate the step on a metric this check does not report."""
            return {'stale': [RemedyStep(action='refresh', condition=Condition(metric='commits', op='>', value=0))]}

    result = _run(_registry(_Conditional()), tmp_path)
    assert result.attempts[0].skipped is True
    assert 'condition not met' in result.attempts[0].detail


def test_an_action_nobody_declares_is_reported(tmp_path: Path) -> None:
    """A chain naming an action that does not exist must not look like a step that ran."""

    class _Missing(_Deployer):
        def remedies(self) -> dict[str, list[RemedyStep]]:
            """Name an action no component declares."""
            return {'stale': [RemedyStep(action='nonexistent')]}

    result = _run(_registry(_Missing()), tmp_path)
    assert result.attempts[0].ok is False
    assert 'no action named' in result.attempts[0].detail


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    """A rehearsal must not perform the act it is rehearsing."""
    seen: list[tuple[list[str], int, str | None]] = []
    result = _run(_registry(_Deployer()), tmp_path, manager=_manager(seen), dry_run=True)
    assert _Deployer.calls == []
    assert seen == []
    assert result.deliveries == []
    assert [attempt.action for attempt in result.attempts] == ['refresh']


def test_a_second_cycle_on_one_target_is_refused_by_the_broker(tmp_path: Path) -> None:
    """THE ONLY LOCK. The kit forbids a pid file or a heartbeat as a second definition of it."""
    broker = Broker()
    with broker.admit('supervise-unit-test', {'seats': 1}, wait_s=0.0), pytest.raises(Exhausted):
        _run(_registry(_Probe()), tmp_path, broker=broker)


def test_a_warranted_notice_reaches_the_channel(tmp_path: Path) -> None:
    """The end of the chain: something was wrong, and a human was told."""
    _Probe.anomalies = [Anomaly(kind='bad', severity=Severity.CRITICAL, message='bad')]
    transport = _Transport()
    config = _config()
    config['alerts'] = {'telegram': {'enabled': True, 'token': 't', 'chat': 'c'}}
    notifier = Notifier(config=config, transport=transport)
    result = _run(_registry(_Probe()), tmp_path, config=config, notifier=notifier)
    assert [delivery.channel for delivery in result.deliveries] == ['telegram']
    assert result.deliveries[0].ok is True
    assert transport.hosts == [('api.telegram.org', 15.0)]


def test_a_condition_that_does_not_hold_holds_the_step(tmp_path: Path) -> None:
    """The gate is data on the step, read against the metrics this cycle measured."""

    class _Conditional(_Deployer):
        def remedies(self) -> dict[str, list[RemedyStep]]:
            """Gate the step on a metric this check does not report."""
            return {'stale': [RemedyStep(action='refresh', condition=Condition(metric='commits', op='>', value=0))]}

    result = _run(_registry(_Conditional()), tmp_path)
    assert result.attempts[0].skipped is True
    assert 'condition not met' in result.attempts[0].detail


def test_the_state_a_cycle_reports_is_the_state_it_wrote(tmp_path: Path) -> None:
    """A caller reading the result must see what a later cycle will read."""
    store = StateStore(tmp_path / 'state.json')
    _Probe.anomalies = [Anomaly(kind='bad', severity=Severity.WARNING, message='bad')]
    result = run_cycle(_registry(_Probe()), store, _config(), _PROJECT, _manager([]), clock=_CLOCK)
    assert result.state == store.read()
    assert result.state['anomalies']['probe.bad']['consecutive'] == 1
