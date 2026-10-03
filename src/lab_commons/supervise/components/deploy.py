"""The deployment component: notice a release, prove it, and say what happened.

THIN ON PURPOSE. Every phase lives in :mod:`lab_commons.supervise.deploy` and every fact about what
is good lives in :mod:`lab_commons.supervise.release`; this file decides WHEN to run them and what
to report. The predecessor spread the same three concerns across a tracking component and a deploy
module and duplicated the chain between its daemon and its AI loop, which is how one release came to
have two meanings depending on who deployed it.

THE CIRCUIT BREAKER IS NOT A BLACKLIST. A release that keeps failing is still retried on the next
cycle -- the user's requirement, and the right one, because the fix for a bad release is usually
another release. What the count buys is ESCALATION: past the tolerance, the alert says "this has
failed five times", which an operator can act on, instead of the same first-time warning arriving
every five minutes forever.

DRIFT IS A HUMAN MISTAKE, so it is alerted and never repaired. A checkout with uncommitted changes
is somebody working; resetting it would destroy that work to make a number go green.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from lab_commons.supervise.component import ActionContext, CheckContext, Component, Handler
from lab_commons.supervise.deploy import Outcome, Plan
from lab_commons.supervise.deploy import deploy as run_deploy
from lab_commons.supervise.deploy import rollback as run_rollback
from lab_commons.supervise.policy import now
from lab_commons.supervise.release import (
    Snapshot,
    failure_streak,
    history,
    record_failure,
    record_success,
    target_of,
)
from lab_commons.supervise.transport import Transport, scheme_transport
from lab_commons.supervise.verdict import Action, Anomaly, CheckResult, RemedyStep, Severity

__all__ = ['Deploy']

#: The spare port a candidate is proven on. Named rather than discovered: a port picked by asking
#: the OS is a port something else can take between the question and the answer.
DEFAULT_PORT: Final = 7002


@dataclass
class Deploy(Component):
    """Watch several repositories, prove a release in isolation, and activate it only once proven.

    Attributes:
        transport: how the health and candidate probes make a connection.

    """

    name = 'deploy'
    description = 'Prove a release in isolation, then activate it; roll back and verify if it fails'

    transport: Transport | None = None

    def check(self, ctx: CheckContext) -> CheckResult:
        """Report a release worth deploying, drift, and a release that keeps failing.

        Args:
            ctx: the component's config and its own state slice.

        Returns:
            An anomaly per thing worth saying, and nothing at all when the target is not
            configured to deploy -- an unconfigured deploy component is off, not broken.

        """
        plan = Plan.from_config(ctx.config, ctx.project)
        if not plan.repositories:
            return CheckResult()
        result = CheckResult()
        known = history(ctx.state)
        result.metrics['known_releases'] = float(len(known))
        if not known:
            # THE WARNING IS ABOUT ROLLBACK, AND IT USED TO RETURN HERE. A snapshot is written by a
            # successful deploy; a deploy runs only on `release_available`; and this early return
            # meant `release_available` was never reached without one. A target that had never
            # deployed could therefore never deploy -- and the host this was written for sat in
            # exactly that state, warning on every cycle while a pushed release waited to be seen.
            # "There is no floor if this fails" is not a reason to refuse the one action that can
            # build one.
            result.anomalies.append(
                Anomaly(
                    kind='no_verified_release',
                    severity=Severity.WARNING,
                    message='no release has been verified on this target, so a failure could not be rolled back',
                    signature='no_verified_release',
                )
            )
        result.anomalies.extend(_drift(ctx, plan))
        wanted = _remote(ctx, plan)
        baseline = known[-1] if known else _checked_out(ctx, plan)
        if wanted is not None and baseline is not None and wanted.key != baseline.key:
            result.anomalies.append(
                Anomaly(
                    kind='release_available',
                    severity=Severity.WARNING,
                    message=f'{plan.main} has {wanted.main[:8]} to deploy',
                    signature=wanted.key,
                )
            )
            streak = failure_streak(ctx.state, wanted)
            if streak >= plan.tolerance:
                result.anomalies.append(
                    Anomaly(
                        kind='release_failing',
                        severity=Severity.CRITICAL,
                        message=f'{wanted.main[:8]} has failed {streak} times; it will keep being retried',
                        signature=f'failing:{wanted.key}',
                    )
                )
        return result

    def remedies(self) -> Mapping[str, list[RemedyStep]]:
        """Declare where each anomaly leads.

        Returns:
            The chains. Drift and a first-time failure are ALERTED rather than acted on, because
            both describe a person's work in progress rather than a state the supervisor may
            change on its own.

        """
        return {
            'release_available': [RemedyStep(action='deploy')],
            'release_failing': [RemedyStep(action='notify', escalate_after=1)],
            'no_verified_release': [RemedyStep(action='notify', escalate_after=2)],
            'drifted': [RemedyStep(action='notify', escalate_after=2)],
        }

    def actions(self) -> Mapping[str, Action]:
        """Declare the two things this component can do.

        Returns:
            The actions, both implemented in Python rather than as commands -- a deployment is a
            sequence with decisions in it, not a line a shell can run.

        """
        return {
            'deploy': Action(description='prove the remote release in isolation, then activate it'),
            'rollback': Action(description='put every repository back at the newest verified snapshot'),
            'notify': Action(description='no-op whose escalation sends the alert'),
        }

    def handlers(self) -> Mapping[str, Handler]:
        """Return the implementations of the declared actions.

        Returns:
            The handlers, keyed by the action names declared above.

        """
        return {'deploy': self._deploy, 'rollback': self._rollback, 'notify': _ignore}

    def _deploy(self, ctx: ActionContext) -> bool:
        """Run the whole sequence, and record what it settled.

        Args:
            ctx: the acting context.

        Returns:
            True when the target is serving the release it was asked for -- which is the OLD
            release when the new one failed and the rollback worked, because that outcome is a
            success for the target even though it is a failure for the release.

        """
        plan = Plan.from_config(ctx.config, ctx.project)
        known = history(ctx.state)
        previous = known[-1] if known else None
        outcome = run_deploy(
            plan,
            ctx,
            previous,
            transport=self.transport or scheme_transport,
            port=int(ctx.config.get('candidate_port', DEFAULT_PORT)),
        )
        return _settle(ctx, plan, outcome, commits=outcome.tried or {})

    def _rollback(self, ctx: ActionContext) -> bool:
        """Put the target back at the newest verified snapshot.

        Args:
            ctx: the acting context.

        Returns:
            Whether the rollback was verified.

        """
        plan = Plan.from_config(ctx.config, ctx.project)
        known = history(ctx.state)
        if not known:
            return False
        return run_rollback(plan, ctx, known[-1], transport=self.transport or scheme_transport).ok


def _settle(ctx: ActionContext, plan: Plan, outcome: Outcome, *, commits: dict[str, str]) -> bool:
    """Record a deployment's outcome and say whether the target is serving.

    Args:
        ctx: the acting context.
        plan: the deployment's plan.
        outcome: what the sequence produced.
        commits: the release that was being tried, which a failure is remembered by.

    Returns:
        True when the target is serving something verified.

    """
    if outcome.commits is None:
        record_failure(ctx.state, target_of(commits), outcome.detail, tolerance=plan.tolerance)
        return False
    verified = target_of(outcome.commits)
    record_success(
        ctx.state,
        Snapshot(main=verified.main, libs=verified.libs, at=now().isoformat()),
        keep=plan.keep,
    )
    return True


def _remote(ctx: CheckContext | ActionContext, plan: Plan) -> Snapshot | None:
    """Return the release the remote now holds, or None when it cannot be read.

    Args:
        ctx: the acting or checking context, for the process manager.
        plan: the deployment's plan.

    Returns:
        The snapshot, or None -- a fetch that failed is not a release to deploy, and this component
        says nothing about it. The check that owns reachability is the probe family's.

    """
    commits: dict[str, str] = {}
    for name in plan.repositories:
        # FETCH FIRST. Reading `origin/main` without one reads whatever the last fetch left, which
        # is how a three-day-old ref came to be reported as the remote's current state twice in one
        # session. A fetch that fails is a `None` here, not a stale answer.
        fetched = ctx.manager.run_capped(
            ['git', '-C', plan.repositories[name], 'fetch', 'origin', '--quiet'],
            memory_max='256M',
            timeout=300,
        )
        if not fetched.ok:
            return None
        head = ctx.manager.run_capped(
            ['git', '-C', plan.repositories[name], 'rev-parse', 'origin/main'],
            memory_max='256M',
            timeout=120,
        )
        if not head.ok:
            return None
        commits[name] = head.out.strip()
    return target_of(commits)


def _checked_out(ctx: CheckContext | ActionContext, plan: Plan) -> Snapshot | None:
    """Return what the target is running now.

    The baseline for a target that has never had a verified release. Without one there is nothing
    to compare the remote against, and every cycle would either redeploy the same commits or --
    as it did before this existed -- never offer a release at all.

    Args:
        ctx: the acting or checking context, for the process manager.
        plan: the deployment's plan.

    Returns:
        The snapshot the checkouts describe, or None when one cannot be read. An unreadable
        checkout is not a baseline, so nothing is concluded from it.

    """
    commits: dict[str, str] = {}
    for name in plan.repositories:
        head = ctx.manager.run_capped(
            ['git', '-C', plan.repositories[name], 'rev-parse', 'HEAD'],
            memory_max='256M',
            timeout=120,
        )
        if not head.ok:
            return None
        commits[name] = head.out.strip()
    return target_of(commits)


def _drift(ctx: CheckContext | ActionContext, plan: Plan) -> list[Anomaly]:
    """Report a checkout with uncommitted changes.

    Args:
        ctx: the acting or checking context.
        plan: the deployment's plan.

    Returns:
        One anomaly per drifted repository, which is alerted and never repaired: a dirty checkout
        is somebody working, and resetting it would destroy that work to make a number go green.

    """
    found: list[Anomaly] = []
    for name, path in plan.repositories.items():
        status = ctx.manager.run_capped(['git', '-C', path, 'status', '--porcelain'], memory_max='256M', timeout=60)
        if status.ok and status.out.strip():
            found.append(
                Anomaly(
                    kind='drifted',
                    severity=Severity.WARNING,
                    message=f'{name} has uncommitted changes; it will be left alone',
                    signature=f'{name}:{status.out.strip()[:80]}',
                )
            )
    return found


def _ignore(_ctx: ActionContext) -> bool:
    """Do nothing, successfully, so an escalation has something to be attached to.

    Args:
        _ctx: the acting context, unused.

    Returns:
        True.

    """
    return True
