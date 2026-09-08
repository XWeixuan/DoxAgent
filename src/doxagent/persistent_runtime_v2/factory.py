"""Environment-backed assembly for the isolated Persistent Runtime V2."""

from __future__ import annotations

import json
from pathlib import Path

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.published_storage import SupabasePublishedDocumentStorage
from doxagent.codex_runtime.repository import (
    CodexRuntimeRepository,
    HybridCodexRuntimeRepository,
    InMemoryCodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.repository import (
    Document3PolicyRepository,
    HybridDocument3PolicyRepository,
    PostgresDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
)

from .projection import RuntimeV2ProjectionOutbox
from .providers import (
    Document3RuntimePolicyProvider,
    PublishedEventLibraryRuntimeProvider,
)
from .repository import (
    InMemoryPersistentRuntimeV2Repository,
    PersistentRuntimeV2Repository,
    SQLitePersistentRuntimeV2Repository,
)
from .service import PersistentRuntimeV2Service
from .transport import BailianRuntimeResponsesClient
from .w3 import CodexW3AgentRunner, PublishedW3ContextProvider


def build_persistent_runtime_v2_service(
    settings: DoxAgentSettings,
) -> PersistentRuntimeV2Service:
    if not settings.persistent_runtime_v2_enabled:
        raise ValueError(
            "Persistent Runtime V2 is disabled; set DOXAGENT_PERSISTENT_RUNTIME_V2_ENABLED=true"
        )
    if not settings.persistent_runtime_v2_strict_mode:
        raise ValueError("Persistent Runtime V2 requires strict mode")
    if settings.persistent_runtime_v2_model != "qwen3.8-flash":
        raise ValueError("Persistent Runtime V2 model is frozen to qwen3.8-flash")
    if settings.persistent_runtime_v2_retry_attempts != 2:
        raise ValueError("Persistent Runtime V2 requires exactly two automatic retries")
    if (
        settings.persistent_runtime_v2_first_retry_delay_seconds != 5
        or settings.persistent_runtime_v2_second_retry_delay_seconds != 10
    ):
        raise ValueError("Persistent Runtime V2 retry delays are frozen to 5s and 10s")
    if not settings.event_library_root:
        raise ValueError("Persistent Runtime V2 requires DOXAGENT_EVENT_LIBRARY_ROOT")
    if settings.ticker_initialization_control_path:
        if not settings.persistent_runtime_v2_w3_enabled:
            raise ValueError("managed initialization requires W3 execution to be configured")
        root = Path(settings.persistent_runtime_v2_w3_prompt_root)
        for relative in (
            "agent.md",
            "skills/uncovered_new.md",
            "skills/revalidate_then_evaluate.md",
        ):
            if not (root / relative).is_file():
                raise ValueError(f"W3 execution asset unavailable: {relative}")

    runtime_repository: PersistentRuntimeV2Repository
    journal = None
    if settings.persistent_runtime_v2_storage_mode == "memory":
        runtime_repository = InMemoryPersistentRuntimeV2Repository()
    else:
        runtime_repository = SQLitePersistentRuntimeV2Repository(
            settings.persistent_runtime_v2_sqlite_path
        )
        if settings.ticker_initialization_control_path:
            from .journal import RuntimeJournal

            journal = RuntimeJournal(settings.persistent_runtime_v2_sqlite_path)

    local_policy = SQLiteDocument3PolicyRepository(settings.codex_runtime_sqlite_path)
    policy_repository: Document3PolicyRepository
    if (
        settings.codex_runtime_storage_mode in {"hybrid", "postgres"}
        and not settings.ticker_initialization_control_path
    ):
        if not settings.database_url:
            raise ValueError("Remote D3 policy storage requires DOXAGENT_DATABASE_URL")
        remote_policy = PostgresDocument3PolicyRepository(settings.database_url)
        policy_repository = HybridDocument3PolicyRepository(
            primary=remote_policy,
            local=local_policy,
        )
    else:
        policy_repository = local_policy

    event_reader = PublishedEventLibraryReader(
        settings.event_library_root,
        market=settings.event_library_market,
    )
    responses = BailianRuntimeResponsesClient(
        api_key=settings.require_dashscope_api_key(),
        base_url=settings.dashscope_chat_base_url,
        model=settings.persistent_runtime_v2_model,
        reasoning_effort=settings.persistent_runtime_v2_reasoning_effort,
        timeout_seconds=settings.persistent_runtime_v2_timeout_seconds,
        session_cache=settings.persistent_runtime_v2_session_cache_enabled,
    )
    projection_outbox = None
    if settings.persistent_runtime_v2_remote_projection_enabled:
        if settings.persistent_runtime_v2_storage_mode != "hybrid":
            raise ValueError("Runtime V2 remote projection requires hybrid storage mode")
        if not settings.database_url:
            raise ValueError("Runtime V2 remote projection requires DOXAGENT_DATABASE_URL")
        projection_outbox = RuntimeV2ProjectionOutbox(
            settings.persistent_runtime_v2_sqlite_path,
            database_url=settings.database_url,
        )
    w3_agent = None
    if settings.persistent_runtime_v2_w3_enabled:
        if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
            raise ValueError("Persistent Runtime W3 requires Codex worker credentials")
        local_runtime = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
        codex_repository: CodexRuntimeRepository
        if (
            settings.codex_runtime_storage_mode == "memory"
            and not settings.ticker_initialization_control_path
        ):
            codex_repository = InMemoryCodexRuntimeRepository()
        elif (
            settings.codex_runtime_storage_mode == "sqlite"
            or settings.ticker_initialization_control_path
        ):
            codex_repository = local_runtime
        else:
            if not settings.database_url:
                raise ValueError("Persistent Runtime W3 remote Codex storage requires database")
            remote_runtime = PostgresCodexRuntimeRepository(
                settings.database_url,
                evidence_repository=local_runtime,
            )
            codex_repository = (
                HybridCodexRuntimeRepository(
                    local=local_runtime,
                    remote=remote_runtime,
                    mirror_remote_runtime_locally=(settings.codex_hybrid_local_mirror_enabled),
                )
                if settings.codex_runtime_storage_mode == "hybrid"
                else remote_runtime
            )
        worker = HttpCodexWorkerClient(
            settings.codex_worker_base_url,
            settings.codex_worker_bearer_token,
            capability_secret=settings.codex_capability_secret,
        )
        published_storage = None
        if (
            settings.codex_published_storage_url
            and settings.codex_published_storage_secret_key
            and not settings.ticker_initialization_control_path
        ):
            published_storage = SupabasePublishedDocumentStorage(
                settings.codex_published_storage_url,
                settings.codex_published_storage_secret_key,
                settings.codex_published_storage_bucket,
            )
        w3_agent = CodexW3AgentRunner(
            journal=journal,
            worker=worker,
            workspace=worker,
            context_provider=PublishedW3ContextProvider(
                runtime_repository=codex_repository,
                policy_repository=policy_repository,
                event_library_reader=event_reader,
                policy_consumption_reader=runtime_repository,
                published_storage=published_storage,
            ),
            prompt_root=Path(settings.persistent_runtime_v2_w3_prompt_root),
            model=settings.persistent_runtime_v2_w3_model,
            model_provider=settings.codex_model_provider,
            effort=settings.persistent_runtime_v2_w3_reasoning_effort,
            timeout_seconds=settings.persistent_runtime_v2_w3_timeout_seconds,
        )
    input_snapshot_loader = None
    if settings.ticker_initialization_control_path:
        from doxagent.ticker_initialization.repository import InitializationRepository
        from doxagent.ticker_initialization.runtime_inputs import ActivatedRuntimeInputs
        from doxagent.v2_control.repository import ControlRepository

        input_snapshot_loader = ActivatedRuntimeInputs(
            InitializationRepository(settings.ticker_initialization_control_path),
            event_reader,
            local_policy,
            runtime_control=ControlRepository(journal) if journal else None,
        )
    if journal and not journal.get("execution", "active"):
        from .execution_bundle import ExecutionBundles
        from .prompts import RuntimeV2PromptSet

        w3_root = Path(settings.persistent_runtime_v2_w3_prompt_root)
        assets = {
            "w3/" + path.relative_to(w3_root).as_posix(): path.read_text(encoding="utf-8")
            for path in w3_root.rglob("*")
            if path.is_file()
        }
        for component in ("event_library", "document3"):
            component_root = Path("prompts/codex_v2") / component
            assets.update(
                {
                    component + "/" + path.relative_to(component_root).as_posix(): path.read_text(
                        encoding="utf-8"
                    )
                    for path in component_root.rglob("*")
                    if path.is_file()
                }
            )
        assets["runtime_models.json"] = json.dumps(
            {
                "w12_model": responses.model,
                "w12_effort": responses.reasoning_effort,
                "w3_model": settings.persistent_runtime_v2_w3_model,
                "w3_effort": settings.persistent_runtime_v2_w3_reasoning_effort,
                "codex_model": settings.codex_model,
                "codex_effort": settings.codex_reasoning_effort,
                "codex_provider": settings.codex_model_provider,
            }
        )
        ExecutionBundles(journal).publish(
            RuntimeV2PromptSet.load(Path(settings.persistent_runtime_v2_prompt_root)), assets=assets
        )
    service = PersistentRuntimeV2Service(
        journal=journal,
        input_snapshot_loader=input_snapshot_loader,
        repository=runtime_repository,
        responses=responses,
        known_events=PublishedEventLibraryRuntimeProvider(event_reader),
        policies=Document3RuntimePolicyProvider(policy_repository),
        prompt_root=Path(settings.persistent_runtime_v2_prompt_root),
        retry_delays_seconds=(
            settings.persistent_runtime_v2_first_retry_delay_seconds,
            settings.persistent_runtime_v2_second_retry_delay_seconds,
        ),
        projection_outbox=projection_outbox,
        w3_agent=w3_agent,
        w3_max_ticker_concurrency=(settings.persistent_runtime_v2_w3_max_ticker_concurrency),
        w3_lease_seconds=settings.persistent_runtime_v2_w3_lease_seconds,
    )
    if journal:
        from .coordinator import RuntimeCoordinator
        from .maintenance import RuntimeMaintenance
        from .selection import WeekendSelection

        service.coordinator = RuntimeCoordinator(
            service,
            journal,
            maintain=RuntimeMaintenance(settings, service, journal),
            select=WeekendSelection(settings, service, journal),
            external_delivery=bool(settings.ticker_initialization_control_path),
        )
    return service
