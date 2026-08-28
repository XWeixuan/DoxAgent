"""Environment-backed D3 service builder without Dashboard coupling."""

from __future__ import annotations

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.config import CodexRuntimeConfig
from doxagent.codex_runtime.published_storage import (
    PublishedDocumentStorage,
    SupabasePublishedDocumentStorage,
)
from doxagent.codex_runtime.repository import (
    CodexRuntimeRepository,
    HybridCodexRuntimeRepository,
    InMemoryCodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.settings import DoxAgentSettings

from .inputs import Document3InputPreparer
from .orchestrator import Document3Orchestrator
from .repository import (
    Document3PolicyRepository,
    HybridDocument3PolicyRepository,
    InMemoryDocument3PolicyRepository,
    PostgresDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
)
from .runner import Document3AgentRunner


def build_document3_orchestrator(settings: DoxAgentSettings) -> Document3Orchestrator:
    config = CodexRuntimeConfig.from_settings(settings)
    if not settings.codex_document3_enabled:
        raise ValueError("D3 is disabled; set DOXAGENT_CODEX_DOCUMENT3_ENABLED=true")
    if not config.enabled or not config.worker_bearer_token or not config.capability_secret:
        raise ValueError("D3 requires the configured Codex worker and capability secret")

    local_runtime = SQLiteCodexRuntimeRepository(config.sqlite_path)
    local_policy = SQLiteDocument3PolicyRepository(config.sqlite_path)
    runtime_repository: CodexRuntimeRepository
    policy_repository: Document3PolicyRepository
    if config.storage_mode == "memory":
        runtime_repository = InMemoryCodexRuntimeRepository()
        policy_repository = InMemoryDocument3PolicyRepository()
    elif config.storage_mode == "sqlite":
        runtime_repository = local_runtime
        policy_repository = local_policy
    else:
        remote_runtime = PostgresCodexRuntimeRepository(
            config.database_url or "", evidence_repository=local_runtime
        )
        remote_policy = PostgresDocument3PolicyRepository(config.database_url or "")
        if config.storage_mode == "hybrid":
            runtime_repository = HybridCodexRuntimeRepository(
                local=local_runtime,
                remote=remote_runtime,
                mirror_remote_runtime_locally=config.hybrid_local_mirror_enabled,
            )
            policy_repository = HybridDocument3PolicyRepository(
                primary=remote_policy,
                local=local_policy,
            )
        else:
            runtime_repository = remote_runtime
            policy_repository = remote_policy

    worker = HttpCodexWorkerClient(
        str(config.worker_base_url),
        config.worker_bearer_token,
        capability_secret=config.capability_secret,
    )
    published_storage: PublishedDocumentStorage | None = None
    if config.published_storage_url and config.published_storage_secret_key:
        published_storage = SupabasePublishedDocumentStorage(
            str(config.published_storage_url),
            config.published_storage_secret_key,
            config.published_storage_bucket,
        )
    event_reader = (
        PublishedEventLibraryReader(
            settings.event_library_root,
            market=settings.event_library_market,
        )
        if settings.event_library_root
        else None
    )
    input_preparer = Document3InputPreparer(
        runtime_repository=runtime_repository,
        policy_repository=policy_repository,
        event_library_reader=event_reader,
        published_storage=published_storage,
    )
    runner = Document3AgentRunner(
        worker=worker,
        workspace=worker,
        model=config.model,
        model_provider=config.model_provider,
        effort=config.reasoning_effort,
        timeout_seconds=config.node_timeout_seconds,
    )
    return Document3Orchestrator(
        input_preparer=input_preparer,
        agent_runner=runner,
        policy_repository=policy_repository,
        runtime_repository=runtime_repository,
        published_storage=published_storage,
    )
