"""Deterministic 60-day Runtime Atomic and Package eligibility projection."""

from __future__ import annotations

from datetime import datetime, timedelta

from cdecr.ports import CDECRRegistry
from doxagent.cdecr_integration.contracts import (
    AtomicRuntimeActivity,
    PackageRuntimeActivity,
    RuntimeActivitySnapshot,
)


def project_runtime_activity(
    *,
    registry: CDECRRegistry,
    runtime_scope: str,
    as_of: datetime,
) -> RuntimeActivitySnapshot:
    """Project eligibility from the latest supporting Source published_at."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = as_of - timedelta(days=60)
    atomics = sorted(
        registry.list_current_atomic_events(limit=10_000), key=lambda item: item.event_id
    )
    activity: list[AtomicRuntimeActivity] = []
    active_ids: set[str] = set()
    for atomic in atomics:
        observed = []
        for mention_id in atomic.mention_ids:
            mention = registry.get_mention(mention_id)
            if mention is None:
                continue
            source = registry.get_source(mention.message_id)
            if source is not None:
                observed.append(source.published_at)
        last_observed_at = max(observed, default=None)
        eligible_until = (
            last_observed_at + timedelta(days=60) if last_observed_at is not None else None
        )
        is_active = last_observed_at is not None and last_observed_at >= cutoff
        if is_active:
            active_ids.add(atomic.event_id)
        activity.append(
            AtomicRuntimeActivity(
                runtime_atomic_id=atomic.event_id,
                last_observed_at=last_observed_at,
                eligible_until=eligible_until,
                is_active=is_active,
            )
        )
    package_activity = []
    for package in sorted(
        registry.list_current_packages(limit=10_000), key=lambda item: item.package_id
    ):
        active_count = len(set(package.member_event_ids) & active_ids)
        package_activity.append(
            PackageRuntimeActivity(
                runtime_package_id=package.package_id,
                active_atomic_count=active_count,
                is_active=active_count > 0,
            )
        )
    return RuntimeActivitySnapshot(
        runtime_scope=runtime_scope,
        as_of=as_of,
        atomics=activity,
        packages=package_activity,
    )
