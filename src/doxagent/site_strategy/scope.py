"""Task-local source provenance and final-publisher access routing."""

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

from .governance_models import SourceAccessScope
from .governance_repository import GovernanceRepository
from .schema import AccessCombination, BrowserProfile, BrowserResidency


@dataclass(frozen=True)
class AccessRoutingContext:
    origin_scope_id: str
    scope_revision: int | None = None


_routing: ContextVar[AccessRoutingContext | None] = ContextVar("site_access_routing", default=None)


def current_routing() -> AccessRoutingContext | None:
    return _routing.get()


@contextmanager
def access_routing(context: AccessRoutingContext):
    token = _routing.set(context)
    try:
        yield
    finally:
        _routing.reset(token)


def source_routed(function):
    """Entry point wrapper: scheduler receives source, enrichment receives job."""

    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        source_or_job = (
            args[0] if args else kwargs.get("source", kwargs.get("job", kwargs.get("request")))
        )
        source = getattr(source_or_job, "source", source_or_job)
        with access_routing(AccessRoutingContext(f"source:{source.source_id}")):
            return await function(self, *args, **kwargs)

    return wrapped


class ScopeResolver:
    def __init__(self, repository):
        self.governance = GovernanceRepository(repository)

    def resolve(self, resolved, origin_scope_id=None, revision=None):
        origin = self.governance.get_scope(origin_scope_id) if origin_scope_id else None
        if origin and revision is not None and origin.revision != revision:
            raise ValueError("source scope revision conflict")
        scope = (
            origin
            if origin
            and origin.owner_site_id == resolved.site_id
            and (resolved.site_id != "generic" or origin.scope_id == self.publisher_key(resolved))
            else self.governance.get_scope(self.publisher_key(resolved))
        )
        if scope is None:
            scopes = self.governance.list_scopes()
            scope = next(
                (
                    s
                    for s in scopes
                    if s.owner_site_id == resolved.site_id
                    and origin_scope_id is None
                    and s.verification_plan.get("default_publisher")
                ),
                None,
            )
            if scope is None:
                if not scopes:
                    return resolved
                scope = self._provision_publisher(resolved, self.publisher_key(resolved))
        members = [scope.primary_identity_id, scope.fallback_identity_id]
        combinations = [
            AccessCombination(id=identity, identity_id=identity, priority=index)
            for index, identity in enumerate(members)
            if identity
        ]
        access = resolved.access.model_copy(update={"combinations": combinations, "overrides": {}})
        return resolved.model_copy(
            update={
                "enabled": resolved.enabled and scope.enabled,
                "runtime_key": f"scope:{scope.scope_id}",
                "access": access,
                "match_evidence": {
                    **resolved.match_evidence,
                    "scope_id": scope.scope_id,
                    "scope_revision": scope.revision,
                },
            }
        )

    @staticmethod
    def publisher_key(resolved):
        name = (
            resolved.site_id if resolved.site_id != "generic" else resolved.match_evidence["host"]
        )
        return f"publisher:{name}"

    def _provision_publisher(self, resolved, scope_id):
        repository = self.governance.repository
        template = next(
            (
                identity
                for c in resolved.access.combinations
                if (identity := repository.get_identity(c.identity_id or c.profile_id or ""))
            ),
            None,
        )
        if template is None:
            raise ValueError("publisher has no approved browser environment")
        suffix = hashlib.sha256(scope_id.encode()).hexdigest()[:16]
        identity_id = f"publisher-{suffix}"
        identity = template.model_copy(
            update={
                "identity_id": identity_id,
                "profile_id": identity_id,
                "revision": 0,
                "owner_scope_id": scope_id,
                "lifecycle": template.lifecycle.model_copy(
                    update={
                        "residency": BrowserResidency.ON_DEMAND,
                        "idle_seconds": 600,
                    }
                ),
            }
        )
        if repository.get_profile(identity_id) is None:
            repository.save_profile(
                BrowserProfile(
                    profile_id=identity_id,
                    directory_key=identity_id,
                    site_id=resolved.site_id,
                    bound_egress_id=identity.egress_id,
                    environment=identity.environment,
                )
            )
        if repository.get_identity(identity_id) is None:
            repository.apply_identity(
                identity, expected_revision=None, actor="lazy-publisher-scope"
            )
        return self.governance.apply_scope(
            SourceAccessScope(
                scope_id=scope_id,
                owner_site_id=resolved.site_id,
                primary_identity_id=identity_id,
                verification_plan={"fallback_state": "UNPROVISIONED", "network_redundancy": "NONE"},
            ),
            None,
        )
