"""Optional business-path taps. Disabled means no directory, DB or network side effect."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from functools import wraps

from .evidence import redact
from .schema import HealthSample, utcnow
from .settings import MaintenanceSettings, enabled

logger = logging.getLogger(__name__)


def optional(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not enabled():
            return
        try:
            return function(*args, **kwargs)
        except Exception:
            logger.warning("source maintenance sample conversion failed", exc_info=False)

    return wrapped


def record(sample: HealthSample) -> None:
    settings = MaintenanceSettings()
    if not settings.enabled or (settings.sources and sample.source_id not in settings.sources):
        return
    from .repository import MaintenanceRepository

    try:
        MaintenanceRepository(settings.database).record_sample(
            HealthSample.model_validate(redact(sample.model_dump(mode="json")))
        )
    except Exception:
        # Telemetry cannot stop acquisition; never log source payloads/credentials.
        logger.warning("source maintenance sample storage failed", exc_info=False)


@optional
def poll(source, binding, result, output, attempted_at: datetime, admission_context=None) -> None:
    metadata = result.acquisition_metadata
    attempts = int(metadata.get("query_attempt_count", 0))
    good = int(metadata.get("query_success_count", 0))
    failures = int(metadata.get("query_failure_count", 0))
    deferred = int(metadata.get("query_deferred_count", 0))
    status = (
        "DEFERRED"
        if result.site_access_deferred
        or (deferred and good == 0 and failures == 0)
        or ("query_attempt_count" in metadata and attempts == 0)
        else "FAILED"
        if (failures and good == 0) or (result.failures and not result.messages and good == 0)
        else "PARTIAL"
        if result.failures or failures
        else "SUCCEEDED"
    )
    failure = result.failures[0] if result.failures else None
    original = failure.original_payload if failure else {}
    evidence = original.get("evidence", {}) if isinstance(original, dict) else {}
    record(
        HealthSample(
            source_id=source.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
            operation_id=output.poll_run_id,
            started_at=attempted_at,
            enabled=source.enabled and binding.enabled and not binding.tombstoned_at,
            window=getattr(admission_context, "mode", "REALTIME"),
            status=status,
            category=evidence.get("category"),
            reason=evidence.get("reason") or (failure.error_code if failure else None),
            query_attempts=attempts,
            query_successes=good,
            query_failures=failures,
            query_deferred=deferred,
            valid_content=bool(result.messages) or status == "SUCCEEDED",
            target_interval_seconds=binding.polling.target_interval_seconds,
        )
    )


@optional
def shared(
    source,
    bindings,
    operation: str,
    started: datetime,
    mode: str,
    result=None,
    error: str | None = None,
) -> None:
    record(
        HealthSample(
            source_id=source.source_id,
            affected_bindings=[b.binding_id for b in bindings],
            operation_id=operation + ":" + started.isoformat(),
            started_at=started,
            window=mode,
            status="FAILED"
            if error
            else "DEFERRED"
            if result.site_access_deferred
            else "PARTIAL"
            if result.failures
            else "SUCCEEDED",
            reason=error,
            site_id=source.site_id,
            valid_content=bool(result.messages) if result else False,
            enabled=source.enabled,
            target_interval_seconds=source.default_polling_config.target_interval_seconds,
        )
    )


@optional
def scheduler(binding, state, now: datetime, in_flight: bool) -> None:
    due = state.target_due_at or state.next_dispatch_at
    if (
        not due
        or in_flight
        or (now - due).total_seconds() <= max(3 * binding.polling.target_interval_seconds, 300)
    ):
        return
    record(
        HealthSample(
            source_id=binding.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
            stage="SCHEDULER",
            operation_id=binding.binding_id + ":" + str(int(now.timestamp()) // 60),
            started_at=now,
            due_at=due,
            status="FAILED",
            target_interval_seconds=binding.polling.target_interval_seconds,
            in_flight=in_flight,
        )
    )


@optional
def poll_failure(binding, code: str, message: str, attempted_at: datetime) -> None:
    operation = f"{binding.binding_id}:{attempted_at.isoformat()}"
    record(
        HealthSample(
            source_id=binding.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
            operation_id=operation,
            started_at=attempted_at,
            enabled=binding.enabled and not binding.tombstoned_at,
            status="FAILED",
            reason=code,
            target_interval_seconds=binding.polling.target_interval_seconds,
        )
    )


@optional
def body(job, message) -> None:
    value = message.metadata.get("media_enrichment") or {}
    trace = value.get("site_access_trace") or []
    last = trace[-1] if trace else {}
    reason = value.get("reason_code")
    full = value.get("outcome") in {"FULL", "SHORT_FULL"}
    record(
        HealthSample(
            source_id=job.source.source_id,
            binding_id=job.binding.binding_id if job.binding else None,
            ticker=job.binding.ticker if job.binding else None,
            stage="BODY",
            operation_id=job.job_id,
            article_key=job.article_id
            or hashlib.sha256((message.url or job.job_id).encode()).hexdigest(),
            started_at=job.created_at,
            completed_at=utcnow(),
            status="SUCCEEDED" if full else "FAILED",
            reason=reason,
            site_id=value.get("site_id") or last.get("site_id"),
            identity_id=last.get("identity_id"),
            valid_content=full,
            accessible_candidate=reason
            not in {
                "subscription_required",
                "login_required",
                "entitlement_missing",
                "non_article_target",
            },
        )
    )
