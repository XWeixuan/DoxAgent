"""Record shared driver failure once per epoch, attributed only to External sites."""

from __future__ import annotations

import json

from .schema import HealthSample, utcnow
from .settings import MaintenanceSettings
from .signals import optional, record


@optional
def observe(service, before: dict | None, after: dict | None, failed: bool = False):
    if not before or not after:
        return
    if (
        before.get("ready")
        and not failed
        and not (
            after.get("epoch") != before.get("epoch")
            and after.get("last_recovery_reason") == "driver_unavailable"
        )
    ):
        return
    settings = MaintenanceSettings()
    if not settings.policy_path:
        return
    policies = json.loads(settings.policy_path.read_text())
    for source_id, policy in policies.items():
        if settings.sources and source_id not in settings.sources:
            continue
        spec = service.repository.get_strategy(policy.get("site_id", ""))
        if not spec:
            continue
        active = service.repository.get_runtime(spec.site_id).active_combination_id
        external = []
        for combo in spec.access.combinations:
            if not combo.enabled or not combo.identity_id:
                continue
            if active and combo.combination_id != active:
                continue
            identity = service.repository.get_identity(combo.identity_id)
            if identity and identity.enabled and identity.runtime_kind.value == "external_chrome":
                external.append(identity.identity_id)
        if external:
            record(
                HealthSample(
                    source_id=source_id,
                    stage="RUNTIME",
                    operation_id="external-driver:" + str(before.get("epoch")),
                    runtime_kind="external_chrome",
                    driver_epoch=before.get("epoch"),
                    driver_recovery_failed=failed or not after.get("ready"),
                    status="FAILED",
                    started_at=utcnow(),
                    completed_at=utcnow(),
                    site_id=spec.site_id,
                    category="RUNTIME_UNAVAILABLE",
                    reason="driver_unavailable",
                )
            )
