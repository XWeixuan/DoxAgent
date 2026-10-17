"""Idempotent, source-by-source identity provisioning. Never copies cookies."""

import asyncio

from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.schema import SourceKind

from .governance_models import (
    ChallengeTask,
    MaintenanceSession,
    OwnedBrowserTarget,
    SourceAccessScope,
)
from .governance_repository import GovernanceRepository
from .schema import BrowserIdentitySpec, BrowserProfile, BrowserResidency, digest_json

# Historical names are retained only where that Profile has one explicit owner.
INHERIT = {
    "barrons_ticker_news": ("dowjones-main", "dowjones-backup"),
    "reuters_site_search": ("digitimes-de-1", "reuters-backup"),
    "digitimes_semiconductors": ("digitimes-nl-1", "digitimes-en-backup"),
    "investing_ticker_news": ("public-markets-nl", "investing-backup"),
    "investorshub_ticker_news": ("investorshub-main", "investorshub-backup"),
    "yahoo_finance_news": ("yahoo-main", "yahoo-backup"),
    "trendforce_news": ("trendforce-1", "trendforce-news-backup"),
    "trendforce_press_releases": ("trendforce-press-main", "trendforce-press-backup"),
    "globenewswire_search": ("globenewswire-1", "globe-search-backup"),
    "globenewswire_semiconductors_rss": ("globe-rss-main", "globe-rss-backup"),
    "digitimes_tw_rss": ("digitimes_tw-1", "digitimes-tw-backup"),
}


async def inspect_legacy_targets(repository, socket_path, extra_identity_ids=()):
    """Only local metadata: never starts Chrome, attaches Playwright or navigates."""
    from .challenge_inspector import _target_ids
    from .schema import BrowserRuntimeKind
    from .supervisor_client import ChromeSupervisorClient

    observations = {}
    slots = asyncio.Semaphore(2)
    client = ChromeSupervisorClient(socket_path, owner_id="site-access")
    pending = {p.challenge_identity_id for p in repository.list_profiles() if p.challenge_url}
    pending.update(extra_identity_ids)

    async def inspect(identity_id):
        identity = repository.get_identity(identity_id) if identity_id else None
        if not identity or identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME:
            return
        try:
            async with slots, asyncio.timeout(3):
                status = await client.status(identity_id)
                value = {
                    "instance_id": status.get("instance_id"),
                    "known_missing": status.get("known_missing") is True,
                    "running": bool(status.get("running")),
                }
                if status.get("running"):
                    value["targets"] = sorted(
                        await asyncio.to_thread(_target_ids, status["cdp_http_url"])
                    )
                observations[identity_id] = value
        except Exception:
            observations[identity_id] = {"diagnostic": "original_target_presence_unverified"}

    await asyncio.gather(*(inspect(identity_id) for identity_id in pending))
    return observations


def registry_revision(repository):
    return digest_json(
        {
            "sites": [
                (h.site_id, h.active_revision, h.enabled)
                for h, _ in repository.list_active_strategies()
            ],
            "identities": [
                (h.identity_id, h.active_revision, h.enabled)
                for h, _ in repository.list_active_identities()
            ],
            "scopes": [(s.scope_id, s.revision) for s in _existing_scopes(repository)],
        }
    )


def _existing_scopes(repository):
    with repository._lock:
        exists = repository._connection.execute(
            "select 1 from sqlite_master where type='table' and name='source_access_scopes'",
        ).fetchone()
    return GovernanceRepository(repository).list_scopes() if exists else []


def crawler_canary(source):
    if source.source_id == "globenewswire_semiconductors_rss":
        return {"url": source.entry_url}
    if source.source_id == "yahoo_finance_news":
        return {
            "url": "https://finance.yahoo.com/quote/MU/news/",
            "recipe_ref": "builtin:yahoo_latest_news@1",
            "recipe_parameters": {"ticker": "MU"},
        }
    if source.source_id == "reuters_site_search":
        return {
            "url": "https://www.reuters.com/site-search/?query=Micron",
            "recipe_ref": "builtin:reuters_search@1",
            "recipe_parameters": {"query": "Micron"},
        }
    if source.source_id == "globenewswire_search":
        return {
            "url": "https://www.globenewswire.com/en/search/keyword/micron/load/more?page=1&pageSize=10"
        }
    if source.source_id in {"investing_ticker_news", "investorshub_ticker_news"}:
        return {
            "url": source.default_parameters["ticker_pages"]["MU"],
            "recipe_parameters": {"ticker": "MU"},
        }
    if source.entry_url and source.kind is SourceKind.CRAWLER:
        result = {"url": source.entry_url.replace("{ticker}", "MU")}
        if source.source_id == "barrons_ticker_news":
            result.update(recipe_ref="builtin:barrons_ticker@1", recipe_parameters={"ticker": "MU"})
        return result
    return None


def _template(repository, site_id):
    spec = repository.get_strategy(site_id)
    if spec:
        for combination in sorted(spec.access.combinations, key=lambda c: c.priority):
            identity = repository.get_identity(
                combination.identity_id or combination.profile_id or ""
            )
            if identity:
                return identity
    raise ValueError(f"site {site_id} has no approved browser template")


def _backup_egress(repository, primary):
    current = repository.get_egress(primary.egress_id)
    if current.node_ref == "DIRECT":
        return primary.egress_id, "NONE"
    regions = GovernanceRepository(repository)
    try:
        current_region = regions.egress_region(current)
    except Exception:
        current_region = None  # Preview against an old schema remains read-only.
    # Names/flags are hints, not verified geography.
    for candidate in repository.list_egresses():
        if (
            candidate.enabled
            and candidate.status == "READY"
            and candidate.observed_ip
            and current.observed_ip
            and candidate.observed_ip != current.observed_ip
            and current_region is not None
            and regions.egress_region(candidate) == current_region
        ):
            return candidate.egress_id, "IP_DISTINCT_SAME_REGION"
    return primary.egress_id, "NONE_PENDING_DISTINCT_EGRESS"


def migration_plan(repository):
    result = []
    claimed = set()
    existing_scopes = {s.scope_id: s for s in _existing_scopes(repository)}
    for source in initial_sources():
        if source.source_id == "yahoo_finance_news":
            source = source.model_copy(update={"site_id": "yahoo_finance"})
        if not source.site_id or repository.get_strategy(source.site_id) is None:
            continue
        scope_id = f"source:{source.source_id}"
        previous = existing_scopes.get(scope_id)
        if previous:
            result.append(
                {"scope": previous.model_dump(mode="json"), "action": "KEEP", "identities": []}
            )
            claimed.update([previous.primary_identity_id, previous.fallback_identity_id])
            continue
        template = _template(repository, source.site_id)
        primary_id, backup_id = INHERIT.get(
            source.source_id,
            (
                f"{source.site_id}-source-main",
                f"{source.site_id}-source-backup",
            ),
        )
        if source.source_id not in INHERIT and template.identity_id not in claimed:
            primary_id = template.identity_id
        if source.source_id == "digitimes_tw_rss":
            primary_id = template.identity_id
        primary = repository.get_identity(primary_id) or template.model_copy(
            update={
                "identity_id": primary_id,
                "profile_id": primary_id,
                "revision": 0,
                "owner_scope_id": scope_id,
            }
        )
        egress_id, redundancy = _backup_egress(repository, primary)
        backup = repository.get_identity(backup_id) or primary.model_copy(
            update={
                "identity_id": backup_id,
                "profile_id": backup_id,
                "egress_id": egress_id,
                "owner_scope_id": scope_id,
                "revision": 0,
            }
        )
        backup = backup.model_copy(
            update={
                "environment": primary.environment,
                "runtime_kind": primary.runtime_kind,
                "browser_release": primary.browser_release,
                "access": primary.access,
                "lifecycle": backup.lifecycle.model_copy(
                    update={
                        "residency": BrowserResidency.ON_DEMAND,
                        "idle_seconds": 120,
                    }
                ),
            }
        )
        identities = []
        for identity in (primary, backup):
            existing = repository.get_identity(identity.identity_id)
            if existing is None:
                identities.append({"action": "CREATE", "spec": identity.model_dump(mode="json")})
            elif existing != identity:
                identities.append(
                    {
                        "action": "UPDATE_ON_COLD_START",
                        "expected_revision": existing.revision,
                        "spec": identity.model_dump(mode="json"),
                    }
                )
        plan = {
            "network_redundancy": redundancy,
            "fallback_state": "UNVERIFIED",
            "contract": "body_v2.2/listing_v1",
            "default_publisher": source.source_id
            not in {
                "trendforce_press_releases",
                "globenewswire_semiconductors_rss",
            },
        }
        spec = repository.get_strategy(source.site_id)
        crawler = crawler_canary(source)
        if crawler:
            plan["crawler"] = crawler
        if spec.auth.verification_url:
            plan["body_url"] = spec.auth.verification_url
        scope = SourceAccessScope(
            scope_id=scope_id,
            source_id=source.source_id,
            owner_site_id=source.site_id,
            enabled=source.enabled,
            primary_identity_id=primary.identity_id,
            fallback_identity_id=backup.identity_id,
            verification_plan=plan,
        )
        result.append(
            {"scope": scope.model_dump(mode="json"), "action": "CREATE", "identities": identities}
        )
        claimed.update([primary.identity_id, backup.identity_id])
    for site_id in ("wsj", "marketwatch"):
        if repository.get_strategy(site_id) is None or f"publisher:{site_id}" in existing_scopes:
            continue
        template = _template(repository, site_id)
        egress_id, redundancy = _backup_egress(repository, template)
        identities = [
            template.model_copy(
                update={
                    "identity_id": f"{site_id}-{role}",
                    "profile_id": f"{site_id}-{role}",
                    "revision": 0,
                    "owner_scope_id": f"publisher:{site_id}",
                    "egress_id": template.egress_id if role == "main" else egress_id,
                    "lifecycle": template.lifecycle.model_copy(
                        update={
                            "residency": BrowserResidency.ON_DEMAND,
                            "idle_seconds": 600 if role == "main" else 120,
                        }
                    ),
                }
            )
            for role in ("main", "backup")
        ]
        result.append(
            {
                "action": "CREATE",
                "scope": SourceAccessScope(
                    scope_id=f"publisher:{site_id}",
                    owner_site_id=site_id,
                    primary_identity_id=f"{site_id}-main",
                    fallback_identity_id=f"{site_id}-backup",
                    verification_plan={
                        "network_redundancy": redundancy,
                        "fallback_state": "UNVERIFIED",
                    },
                ).model_dump(mode="json"),
                "identities": [
                    {"action": "CREATE", "spec": i.model_dump(mode="json")} for i in identities
                ],
            }
        )
    return {
        "expected_revision": registry_revision(repository),
        "scopes": result,
        "cookies_copied": False,
        "chrome_restarted": False,
    }


def import_legacy_state(repository, scope, observations=None):
    """Preserve facts, not READY claims. No navigation, cookie copies or role reset."""
    store = GovernanceRepository(repository)
    spec = repository.get_strategy(scope.owner_site_id)
    old = repository.get_runtime(scope.owner_site_id)
    state = repository.get_runtime(f"scope:{scope.scope_id}")
    members = [scope.primary_identity_id, scope.fallback_identity_id]
    for identity_id in filter(None, members):
        identity = repository.get_identity(identity_id)
        profile = repository.get_profile(identity.profile_id)
        for combination in spec.access.combinations:
            if (combination.identity_id or combination.profile_id) == identity_id:
                health = old.combinations.get(combination.combination_id)
                if health and identity_id not in state.combinations:
                    state.combinations[identity_id] = health.model_copy(deep=True)
                    # Legacy liveness never certifies a cold fallback.
                    state.combinations[identity_id].capabilities = {}
        current_task = store.current_challenge(identity_id)
        if current_task:
            if current_task.scope_id != scope.scope_id:
                store.save_challenge(current_task.model_copy(update={"scope_id": scope.scope_id}))
                for session in store.list_sessions():
                    if session.task_id == current_task.task_id:
                        store.save_session(session.model_copy(update={"scope_id": scope.scope_id}))
            continue
        if not profile.challenge_url:
            continue
        if profile.challenge_identity_id not in (None, identity_id):
            continue
        observed = (observations or {}).get(identity_id, {})
        runtime = repository.get_identity_runtime(identity_id)
        instance_id = (
            observed.get("instance_id") or runtime.instance_id or f"unverified:{identity_id}"
        )
        target_id = profile.challenge_target_id
        present = (target_id in observed["targets"]) if "targets" in observed else None
        if observed.get("known_missing"):
            present = False
        target = store.save_target(
            OwnedBrowserTarget(
                identity_id=identity_id,
                browser_instance_id=instance_id,
                worker_epoch=runtime.generation,
                target_id=target_id,
                state="LOST" if present is False else "LIVE" if target_id else "ALLOCATING",
            )
        )
        task = store.handoff_challenge(
            ChallengeTask(
                task_id=profile.maintenance_session_id or f"challenge_legacy_{identity_id}",
                scope_id=scope.scope_id,
                identity_id=identity_id,
                site_id=scope.owner_site_id,
                purpose="BODY",
                operation_id=f"migration:{scope.scope_id}",
                original_url=profile.challenge_url,
                original_target_id=target_id,
                browser_instance_id=instance_id,
                allocation_id=target.allocation_id,
                state="TARGET_LOST" if present is False or not target_id else "WAITING_HUMAN",
                reason="legacy_target_confirmed"
                if present
                else "legacy_target_presence_unverified",
            )
        )
        store.save_session(
            MaintenanceSession(
                kind="CHALLENGE",
                scope_id=scope.scope_id,
                identity_id=identity_id,
                site_id=scope.owner_site_id,
                task_id=task.task_id,
                allocation_id=target.allocation_id,
                target_id=target_id,
                browser_instance_id=instance_id,
                control_token=task.task_id,
                state="RELEASED",
            )
        )
    # Only an inherited main route is accepted automatically; an old, unverified
    # fallback must pass the new cold contract before becoming active.
    if state.active_combination_id is None:
        state.active_combination_id = scope.primary_identity_id
    repository.save_runtime(state, expected_generation=state.generation)


def apply_migration(repository, plan, *, expected_revision, backup_path, observations=None):
    if (
        registry_revision(repository) != expected_revision
        or plan["expected_revision"] != expected_revision
    ):
        raise RuntimeError("Registry changed since preview; generate a new migration plan")
    launch_fields = {"runtime_kind", "profile_id", "egress_id", "environment", "browser_release"}
    for item in plan["scopes"]:
        scope = SourceAccessScope.model_validate(item["scope"])
        for member in (scope.primary_identity_id, scope.fallback_identity_id):
            identity = repository.get_identity(member) if member else None
            profile = repository.get_profile(identity.profile_id) if identity else None
            if (
                profile
                and profile.challenge_url
                and profile.challenge_site_id
                and profile.challenge_site_id != scope.owner_site_id
            ):
                raise RuntimeError(
                    f"finish the existing cross-site challenge before assigning {member}"
                )
        for update in item["identities"]:
            if update["action"] != "UPDATE_ON_COLD_START":
                continue
            identity = BrowserIdentitySpec.model_validate(update["spec"])
            before = repository.get_identity(identity.identity_id)
            changed = before.model_dump(include=launch_fields) != identity.model_dump(
                include=launch_fields
            )
            if changed:
                profile = repository.get_profile(identity.profile_id)
                observation = (observations or {}).get(identity.identity_id, {})
                if (
                    profile.maintenance_session_id
                    or observation.get("running") is not False
                    or observation.get("known_missing") is not True
                ):
                    raise RuntimeError(
                        f"confirm cold identity before migration: {identity.identity_id}"
                    )
    repository.backup_to(backup_path)
    store = GovernanceRepository(repository)
    for item in plan["scopes"]:
        if item["action"] == "KEEP":
            continue
        scope = SourceAccessScope.model_validate(item["scope"])
        for update in item["identities"]:
            identity = BrowserIdentitySpec.model_validate(update["spec"])
            profile = repository.get_profile(identity.profile_id)
            if profile is None:
                repository.save_profile(
                    BrowserProfile(
                        profile_id=identity.profile_id,
                        directory_key=identity.profile_id,
                        site_id=scope.owner_site_id,
                        bound_egress_id=identity.egress_id,
                        environment=identity.environment,
                    )
                )
            elif profile.environment != identity.environment:
                repository.save_profile(
                    profile.model_copy(update={"environment": identity.environment})
                )
            repository.apply_identity(
                identity,
                expected_revision=update.get("expected_revision"),
                actor="source-isolation-migration",
            )
        stored = store.apply_scope(scope, expected_revision=None)
        import_legacy_state(repository, stored, observations)
    return {
        "revision": registry_revision(repository),
        "backup": str(backup_path),
        "scopes": len(store.list_scopes()),
        "fallbacks_certified": 0,
    }
