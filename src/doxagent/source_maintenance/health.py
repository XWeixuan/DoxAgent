"""Deterministic trigger rules: no-news, off-window and pure deferral are not success."""

from collections import defaultdict
from datetime import datetime, timedelta

from .schema import HealthSample, Incident

HUMAN_REASONS = {
    "login_required",
    "reauth_required",
    "subscription_required",
    "entitlement_missing",
    "challenge_required",
    "AUTH_REQUIRED",
    "ACCESS_CHALLENGE",
}


def scope(sample: HealthSample, failure: str) -> str:
    if sample.runtime_kind == "external_chrome" and failure == "DRIVER":
        return f"RUNTIME:external_chrome:{sample.driver_epoch}"
    if sample.identity_id and failure in {"DRIVER", "CAPACITY"}:
        return f"IDENTITY:{sample.identity_id}:{failure}"
    return f"SOURCE:{sample.source_id}:{sample.stage}"


def diagnose(samples: list[HealthSample], now: datetime) -> list[Incident]:
    crashes = [
        s
        for s in samples
        if s.stage == "RUNTIME"
        and s.status == "FAILED"
        and s.runtime_kind == "external_chrome"
        and s.enabled
        and s.eligible
        and now - timedelta(minutes=30) <= s.completed_at <= now
    ]
    latest_crash = max(crashes, key=lambda s: (s.completed_at, s.driver_epoch or 0), default=None)
    groups: dict[str, list[HealthSample]] = defaultdict(list)
    classes: dict[str, str] = {}
    for sample in sorted(samples, key=lambda s: s.completed_at):
        if not sample.enabled or not sample.eligible or sample.completed_at > now:
            continue
        failure = (
            "HUMAN"
            if sample.reason in HUMAN_REASONS or sample.category in HUMAN_REASONS
            else "DRIVER"
            if sample.stage == "RUNTIME" and sample.status == "FAILED"
            else "CAPACITY"
            if sample.status == "DEFERRED"
            else "SCHEDULER_STALE"
            if sample.stage == "SCHEDULER"
            else sample.stage
        )
        key = scope(sample, failure)
        groups[key].append(sample)
        classes[key] = failure
    incidents = []
    for key, values in groups.items():
        values.sort(key=lambda s: s.completed_at)
        failure = classes[key]
        # A real success resets the failure episode; absence of content does not negate success.
        last_good = max((s.completed_at for s in values if s.status == "SUCCEEDED"), default=None)
        bad = [
            s
            for s in values
            if s.status != "SUCCEEDED" and (last_good is None or s.completed_at > last_good)
        ]
        if failure == "CAPACITY" and key.startswith("SOURCE:"):
            actual_failures = [s for s in bad if s.status in {"FAILED", "PARTIAL"}]
            if actual_failures:
                last_failure = actual_failures[-1]
                failure = "HUMAN" if last_failure.reason in HUMAN_REASONS else last_failure.stage
        if failure == "BODY":
            bad = [
                s
                for s in values
                if s.status == "FAILED"
                and s.accessible_candidate
                and s.completed_at >= now - timedelta(minutes=15)
            ]
        if not bad:
            continue
        if failure == "ACQUISITION" and (now - bad[-1].completed_at).total_seconds() > max(
            3 * bad[-1].target_interval_seconds, 300
        ):
            continue
        affected = bad[:]
        unique = {
            (s.article_key if s.stage == "BODY" else s.operation_id, s.source_id): s for s in bad
        }
        bad = list(unique.values())
        triggered = False
        if failure == "HUMAN":
            triggered = True
        elif failure == "DRIVER":
            if latest_crash and key != scope(latest_crash, "DRIVER"):
                continue
            recent_bad = [s for s in bad if s.completed_at >= now - timedelta(minutes=30)]
            recent = {s.operation_id for s in recent_bad}
            triggered = (
                any(s.driver_recovery_failed for s in recent_bad)
                or len(recent) >= 3
                or len({s.operation_id for s in crashes}) >= 3
            )
        elif failure == "SCHEDULER_STALE":
            triggered = any(
                s.due_at
                and not s.in_flight
                and (now - s.due_at).total_seconds() > max(3 * s.target_interval_seconds, 300)
                for s in bad
            )
        elif failure == "BODY":
            recent = [
                s
                for s in values
                if s.completed_at >= now - timedelta(minutes=15)
                and s.accessible_candidate
                and s.article_key
            ]
            eligible = {s.article_key: s for s in recent}
            failed = [s for s in eligible.values() if s.status == "FAILED"]
            triggered = len(failed) >= 3 and len(failed) / max(1, len(eligible)) >= 0.8
        else:
            attempts = {s.operation_id for s in bad if s.status in {"FAILED", "PARTIAL"}}
            triggered = (bad[-1].completed_at - bad[0].completed_at).total_seconds() >= 900 and (
                len(attempts) >= 3 or failure == "CAPACITY"
            )
        if triggered:
            incidents.append(
                Incident(
                    resource_key=key,
                    failure_class=failure,
                    source_ids=sorted({s.source_id for s in affected}),
                    affected_bindings=sorted(
                        {
                            b
                            for s in affected
                            for b in ([s.binding_id] if s.binding_id else s.affected_bindings)
                        }
                    ),
                    first_observed_at=bad[0].completed_at,
                    last_observed_at=bad[-1].completed_at,
                    context={
                        "sample_ids": [s.sample_id for s in affected],
                        "last_success": last_good.isoformat() if last_good else None,
                        "category": bad[-1].category,
                        "reason": bad[-1].reason,
                        "stage": bad[-1].stage,
                    },
                )
            )
    return incidents


def recovered(samples: list[HealthSample], incident: Incident, *, since: datetime) -> bool:
    if incident.context.get("stage") == "BODY":
        required = 1 if incident.failure_class == "HUMAN" else 3
        for source in incident.source_ids:
            latest = {
                s.article_key: s
                for s in sorted(samples, key=lambda s: s.completed_at)
                if s.source_id == source
                and s.stage == "BODY"
                and s.completed_at >= since
                and s.eligible
                and s.enabled
                and s.article_key
            }
            if (
                len([s for s in latest.values() if s.status == "SUCCEEDED" and s.valid_content])
                < required
            ):
                return False
            if any(s.status == "FAILED" and s.accessible_candidate for s in latest.values()):
                return False
        return True
    relevant = [
        s
        for s in samples
        if s.completed_at >= since
        and s.eligible
        and s.enabled
        and s.source_id in incident.source_ids
        and s.stage == "ACQUISITION"
    ]
    targets = incident.affected_bindings or incident.source_ids
    for target in targets:
        rows = [
            s
            for s in relevant
            if (s.binding_id or s.source_id) == target
            or target in s.affected_bindings
            or (not incident.affected_bindings and s.source_id == target)
        ]
        rows.sort(key=lambda s: s.completed_at)
        distinct = {s.operation_id: s for s in rows}
        last = list(distinct.values())[-3:]
        if len(last) < 3 or any(s.status != "SUCCEEDED" for s in last):
            return False
    bodies = {
        (s.source_id, s.article_key or s.operation_id): s
        for s in sorted(samples, key=lambda s: s.completed_at)
        if s.completed_at >= since
        and s.eligible
        and s.source_id in incident.source_ids
        and s.stage == "BODY"
        and s.accessible_candidate
    }
    if any(s.status == "FAILED" for s in bodies.values()):
        return False
    return True
