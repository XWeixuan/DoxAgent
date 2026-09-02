from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.message_bus_v2.adapters import AdapterLoadError, AdapterRegistry
from doxagent.message_bus_v2.compiler import compile_stream_item
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler, SchedulerGroupLimiter
from doxagent.message_bus_v2.schema import (
    PollContext,
    PollingConfig,
    PollResult,
    PublicationMode,
    RawMessage,
    RawMessageInput,
    RawProcessingStatus,
    SchedulerConstraints,
    SourceDefinition,
    SourceKind,
    StreamingConfig,
    TickerMonitoringStatus,
    UpdateActor,
    canonical_json,
    content_hash_for,
    identity_key_for,
    new_id,
    sha256_text,
    source_item_key_for,
)
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.models import AgentName, ResultStatus
from doxagent.persistent_runtime import (
    InMemoryPersistentRuntimeRepository,
    PersistentRuntimeExecutionService,
)
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus, SourceMessageEnvelope
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentBundle,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    MonitorMode,
    UnifiedRuntimeSchedulerService,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.monitoring import MonitoringToolClient
from doxagent.tools.schema import ToolRequest

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _bus(path: Path) -> tuple[MessageBusV2Repository, MessageBusV2Service]:
    repository = MessageBusV2Repository(path)
    service = MessageBusV2Service(repository)
    service.bootstrap()
    return repository, service


def _input(
    external_id: str,
    *,
    body: str | None = None,
    published_at: datetime = NOW,
) -> RawMessageInput:
    return RawMessageInput(
        external_id=external_id,
        title=f"Title {external_id}",
        body=body or f"Body {external_id}",
        source="Reuters",
        url=f"https://example.test/messages/{external_id}",
        published_at=published_at,
        raw_payload={"id": external_id, "body": body or f"Body {external_id}"},
    )


def test_bootstrap_registry_profile_and_ticker_materialization(tmp_path: Path) -> None:
    repository, service = _bus(tmp_path / "bus.sqlite3")

    assert {source.source_id for source in repository.list_sources()} == {
        "benzinga_news",
        "finnhub_company_news",
        "stocktwits_messages",
        "tikhub_x_search",
        "tikhub_x_user_posts",
        "newswire_rss",
    }
    profile = repository.get_default_profile("default")
    assert profile is not None
    assert [entry.source_id for entry in profile.entries] == [
        "benzinga_news",
        "finnhub_company_news",
    ]

    state = service.start_ticker("mu", actor=UpdateActor.AGENT)
    bindings = repository.list_bindings(ticker="MU")
    assert state.status is TickerMonitoringStatus.RUNNING
    assert {binding.source_id for binding in bindings} == {
        "benzinga_news",
        "finnhub_company_news",
    }
    assert all(binding.polling.target_interval_seconds == 60 for binding in bindings)

    # Profiles are materialized templates, not live parents.
    service.save_default_profile(profile.model_copy(update={"entries": []}))
    assert len(repository.list_bindings(ticker="MU")) == 2


def test_disabled_flag_has_no_v1_fallback_and_does_not_bootstrap_v2_db(
    tmp_path: Path,
) -> None:
    database = tmp_path / "disabled.sqlite3"
    scheduler = UnifiedRuntimeSchedulerService.from_settings(
        DoxAgentSettings(
            _env_file=None,
            runtime_scheduler_storage_mode="memory",
            message_bus_v2_enabled=False,
            message_bus_v2_sqlite_path=str(database),
            persistent_runtime_v2_enabled=False,
            dashscope_api_key="test-only-no-call",
        )
    )
    assert scheduler.message_bus_v2_enabled is False
    assert scheduler.message_bus_v2_service is None
    assert scheduler._legacy_monitoring_service_enabled is False
    assert scheduler.monitoring_service is None
    assert not database.exists()


async def test_ticker_local_dedupe_revision_and_bootstrap(tmp_path: Path) -> None:
    repository, service = _bus(tmp_path / "bus.sqlite3")
    source = service.require_source("benzinga_news")
    for ticker in ("MU", "NVDA"):
        service.start_ticker(ticker)
        binding = repository.get_binding(f"{ticker}:benzinga_news")
        assert binding is not None
        baseline = await service.accept_poll_result(
            source=source,
            binding=binding,
            result=PollResult(messages=[_input("shared-old")]),
            attempted_at=NOW,
        )
        assert baseline.bootstrap_suppressed_count == 1
        live = await service.accept_poll_result(
            source=source,
            binding=binding,
            result=PollResult(messages=[_input("shared-live")]),
            attempted_at=NOW + timedelta(minutes=1),
        )
        assert live.inserted_count == 1
        assert live.published_count == 1

    assert repository.latest_stream_offset("MU") == 1
    assert repository.latest_stream_offset("NVDA") == 1

    mu_binding = repository.get_binding("MU:benzinga_news")
    assert mu_binding is not None
    duplicate = await service.accept_message(
        source=source,
        binding=mu_binding,
        message=_input("shared-live"),
        bootstrap=False,
    )
    revision = await service.accept_message(
        source=source,
        binding=mu_binding,
        message=_input("shared-live", body="Corrected business content"),
        bootstrap=False,
    )
    assert duplicate.decision.value == "duplicate"
    assert revision.decision.value == "revision"
    assert repository.latest_stream_offset("MU") == 2


async def test_buffer_compilation_restart_and_independent_cursors(tmp_path: Path) -> None:
    database = tmp_path / "bus.sqlite3"
    repository, service = _bus(database)
    service.start_ticker("MU")
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    binding = service.update_binding(
        binding.binding_id,
        {
            "streaming": {
                "publication_mode": "buffered",
                "buffer": {
                    "max_items": 2,
                    "max_wait_seconds": 60,
                    "max_compiled_body_chars": 120_000,
                },
            }
        },
        actor=UpdateActor.AGENT,
    )
    source = service.require_source(binding.source_id)
    first = await service.accept_message(
        source=source,
        binding=binding,
        message=_input("a", published_at=NOW),
        bootstrap=False,
    )
    second = await service.accept_message(
        source=source,
        binding=binding,
        message=_input("b", published_at=NOW + timedelta(seconds=5)),
        bootstrap=False,
    )
    assert first.stream_item_ids == []
    assert len(second.stream_item_ids) == 1

    item = repository.read_stream("MU", after_offset=0)[0]
    compiled = compile_stream_item(item)
    envelope = SourceMessageEnvelope.from_stream_item(item)
    assert item.item.member_count == 2
    assert "[MESSAGE 1]" in compiled.body and "[MESSAGE 2]" in compiled.body
    assert "source: Reuters" in compiled.body
    assert compiled.latest.standard_message_id == item.members[1].standard_message_id
    assert envelope.source_message_id == item.members[1].standard_message_id
    assert envelope.url == item.members[1].url
    assert envelope.published_at == item.members[1].published_at
    assert envelope.member_count == 2
    assert envelope.snapshot.title is None
    assert envelope.snapshot.body == compiled.body

    service.commit_stream("consumer-a", item)
    assert service.pending_stream("consumer-a", "MU") == []
    assert len(service.pending_stream("consumer-b", "MU")) == 1

    third = await service.accept_message(
        source=source,
        binding=binding,
        message=_input("c", published_at=NOW + timedelta(seconds=10)),
        bootstrap=False,
    )
    assert third.stream_item_ids == []
    repository.close()

    reopened = MessageBusV2Repository(database)
    recovered_service = MessageBusV2Service(reopened)
    assert len(reopened.list_buffer(binding.binding_id)) == 1
    flushed = recovered_service.flush_due_buffers(now=datetime.now(UTC) + timedelta(seconds=61))
    assert len(flushed) == 1
    assert reopened.latest_stream_offset("MU") == 2
    assert reopened.get_consumer_offset("consumer-a", "MU").stream_offset == 1


def test_pending_raw_recovery_is_idempotent_and_transactional(tmp_path: Path) -> None:
    repository, service = _bus(tmp_path / "bus.sqlite3")
    service.start_ticker("MU")
    source = service.require_source("benzinga_news")
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    value = _input("crash-recovery")
    raw = RawMessage(
        raw_message_id=new_id("raw"),
        ticker="MU",
        source_id=source.source_id,
        binding_id=binding.binding_id,
        source_definition_version=source.version,
        external_id=value.external_id,
        source_item_key=source_item_key_for(source.source_id, value),
        identity_key=identity_key_for(source.source_id, value),
        content_hash=content_hash_for(value),
        raw_hash=sha256_text(canonical_json(value.raw_payload)),
        title=value.title,
        body=value.body,
        source=value.source or source.display_name,
        url=value.url,
        published_at=value.published_at,
        collected_at=NOW,
        raw_payload=value.raw_payload,
        streaming_config=StreamingConfig(publication_mode=PublicationMode.IMMEDIATE),
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    repository.record_raw(raw)

    assert service.retry_pending_raw() == 1
    assert repository.latest_stream_offset("MU") == 1
    completed = repository.get_raw(raw.raw_message_id)
    assert completed is not None
    assert completed.processing_status is RawProcessingStatus.COMPLETED

    repository.save_raw(
        completed.model_copy(update={"processing_status": RawProcessingStatus.PROCESSING})
    )
    assert service.retry_pending_raw() == 1
    assert repository.latest_stream_offset("MU") == 1


async def test_hard_delete_flushes_buffer_and_preserves_immutable_history(
    tmp_path: Path,
) -> None:
    repository, service = _bus(tmp_path / "bus.sqlite3")
    service.start_ticker("MU")
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    binding = service.update_binding(
        binding.binding_id,
        {
            "streaming": {
                "publication_mode": "buffered",
                "buffer": {"max_items": 10, "max_wait_seconds": 600},
            }
        },
        actor=UpdateActor.AGENT,
    )
    await service.accept_message(
        source=service.require_source(binding.source_id),
        binding=binding,
        message=_input("before-delete"),
        bootstrap=False,
    )

    result = service.hard_delete_source(
        "benzinga_news", actor=UpdateActor.AGENT, reason="obsolete"
    )
    assert len(result.flushed_stream_item_ids) == 1
    assert repository.get_source("benzinga_news") is None
    assert repository.get_binding(binding.binding_id, include_tombstoned=True) is None
    assert len(repository.list_raw(ticker="MU")) == 1
    assert len(repository.list_standard(ticker="MU")) == 1
    assert repository.latest_stream_offset("MU") == 1
    assert repository.list_source_revisions("benzinga_news")
    assert all(
        entry.source_id != "benzinga_news"
        for entry in repository.get_default_profile("default").entries  # type: ignore[union-attr]
    )


async def test_poll_failure_since_binding_alert_and_group_deduplication(
    tmp_path: Path,
) -> None:
    repository, service = _bus(tmp_path / "alerts.sqlite3")
    source = service.require_source("benzinga_news")
    bindings = []
    for ticker in ("MU", "NVDA", "AMD"):
        service.start_ticker(ticker)
        binding = repository.get_binding(f"{ticker}:benzinga_news")
        assert binding is not None
        bindings.append(
            service.update_binding(
                binding.binding_id,
                {"polling": {"target_interval_seconds": 60, "alert_after_seconds": 10}},
                actor=UpdateActor.SYSTEM,
            )
        )

    for binding in bindings:
        service.record_poll_failure(
            binding,
            code="provider_down",
            message="provider unavailable",
            attempted_at=NOW,
        )
    alerts = repository.list_alerts(active_only=True)
    aggregate = next(alert for alert in alerts if alert.code == "source_poll_failure_aggregate")
    assert aggregate.metadata["affected_tickers"] == ["AMD", "MU", "NVDA"]
    assert not any(alert.alert_key.startswith("poll_failure:") for alert in alerts)

    first = bindings[0]
    service.record_poll_failure(
        first,
        code="provider_down",
        message="provider unavailable",
        attempted_at=NOW + timedelta(seconds=11),
    )
    assert any(
        alert.alert_key == f"poll_failure:{first.binding_id}"
        for alert in repository.list_alerts(active_only=True)
    )

    await service.accept_poll_result(
        source=source,
        binding=first,
        result=PollResult(),
        attempted_at=NOW + timedelta(seconds=12),
    )
    active = repository.list_alerts(active_only=True)
    assert not any(alert.alert_key == f"poll_failure:{first.binding_id}" for alert in active)
    assert not any(alert.code == "source_poll_failure_aggregate" for alert in active)


async def test_scheduler_group_limiter_spacing_and_rephase_for_1_10_50(
    tmp_path: Path,
) -> None:
    clock_value = [0.0]
    request_times: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        clock_value[0] += seconds

    limiter = SchedulerGroupLimiter(
        SchedulerConstraints(minimum_request_gap_seconds=2, max_concurrency=5),
        clock=lambda: clock_value[0],
        sleep=fake_sleep,
        on_request=lambda: request_times.append(clock_value[0]),
    )

    async def acquire_once() -> None:
        async with limiter.permit():
            await asyncio.sleep(0)

    await asyncio.gather(*(acquire_once() for _ in range(10)))
    assert request_times == [float(index * 2) for index in range(10)]

    for count in (1, 10, 50):
        repository, service = _bus(tmp_path / f"scheduler-{count}.sqlite3")
        source = SourceDefinition(
            source_id="stress_source",
            display_name="Stress Source",
            kind=SourceKind.API,
            adapter_ref="builtin:benzinga_news",
            parameter_schema={"type": "object", "additionalProperties": False},
            scheduler_group="stress",
            scheduler_constraints=SchedulerConstraints(
                minimum_request_gap_seconds=10,
                max_concurrency=1,
            ),
        )
        service.register_source(source)
        for index in range(count):
            ticker = f"T{index:02d}"
            service.configure_binding(
                ticker=ticker,
                source_id=source.source_id,
                polling=PollingConfig(target_interval_seconds=60).model_dump(),
                actor=UpdateActor.SYSTEM,
            )
            repository.save_ticker_state(
                service.start_ticker(ticker).model_copy(
                    update={"status": TickerMonitoringStatus.RUNNING}
                )
            )
        settings = DoxAgentSettings(_env_file=None)
        registry = AdapterRegistry(settings, adapter_root=tmp_path / "adapters")
        scheduler = GlobalPollScheduler(repository, service, registry)
        eligible = scheduler._eligible_bindings(NOW)
        stress_eligible = [pair for pair in eligible if pair[0].source_id == "stress_source"]
        scheduler._initialize_due_slots(stress_eligible, NOW)
        due_values = [
            repository.get_poll_state(binding).next_dispatch_at
            for _, binding in stress_eligible
        ]
        assert len(due_values) == count
        assert min(due_values) == NOW
        assert max(due_values) < NOW + timedelta(seconds=60)
        scheduler._update_capacity_alerts(stress_eligible)
        active_alerts = repository.list_alerts(active_only=True)
        assert bool(active_alerts) is (count > 6)

        if count == 10:
            before = due_values
            service.configure_binding(
                ticker="T10",
                source_id=source.source_id,
                actor=UpdateActor.SYSTEM,
            )
            repository.save_ticker_state(service.start_ticker("T10"))
            updated = [
                pair
                for pair in scheduler._eligible_bindings(NOW)
                if pair[0].source_id == "stress_source"
            ]
            scheduler._initialize_due_slots(updated, NOW + timedelta(seconds=1))
            after = [
                repository.get_poll_state(binding).next_dispatch_at for _, binding in updated
            ]
            assert len(after) == 11
            assert after != before
        await registry.close()


async def test_dynamic_adapter_loading_is_root_scoped(tmp_path: Path) -> None:
    adapter_root = tmp_path / "adapters"
    adapter_root.mkdir()
    script = adapter_root / "custom.py"
    script.write_text(
        "from doxagent.message_bus_v2.schema import PollResult\n"
        "class Adapter:\n"
        "    async def poll(self, context):\n"
        "        return PollResult()\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    registry = AdapterRegistry(DoxAgentSettings(_env_file=None), adapter_root=adapter_root)
    adapter = registry.resolve("file:custom.py:build_adapter", source_version=1)
    assert callable(adapter.poll)
    with pytest.raises(AdapterLoadError, match="escapes"):
        registry.resolve("file:../outside.py:build_adapter", source_version=1)
    await registry.close()


async def test_all_six_builtin_adapters_with_fixed_responses(tmp_path: Path) -> None:
    timestamp = "2026-09-01T12:00:00Z"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/v2/news"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "bz-1",
                        "title": "Benzinga title",
                        "body": "Benzinga body",
                        "author": "Reuters",
                        "url": "https://example.test/bz-1",
                        "created": timestamp,
                    }
                ],
            )
        if path.endswith("/company-news"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 2,
                        "headline": "Finnhub title",
                        "summary": "Finnhub body",
                        "source": "AP",
                        "url": "https://example.test/fh-2",
                        "datetime": int(NOW.timestamp()),
                    }
                ],
            )
        if "stocktwits" in path or "streams/symbol" in path:
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "st-3",
                            "body": "Stocktwits body",
                            "created_at": timestamp,
                            "user": {"username": "person"},
                        }
                    ]
                },
            )
        if path.endswith("/fetch_search_timeline"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "rest_id": "x-4",
                            "full_text": "X search body",
                            "created_at": timestamp,
                            "screen_name": "searcher",
                        }
                    ]
                },
            )
        if path.endswith("/fetch_user_post_tweet"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "rest_id": "x-5",
                            "full_text": "X user body",
                            "created_at": timestamp,
                            "screen_name": "issuer",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            text=(
                "<rss><channel><item><guid>rss-6</guid><title>RSS title</title>"
                "<description>RSS body</description><link>https://example.test/rss-6</link>"
                "<pubDate>Tue, 01 Sep 2026 12:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = DoxAgentSettings(
        _env_file=None,
        benzinga_api_key="test",
        finnhub_api_key="test",
        tikhub_api_key="test",
        stocktwits_rapidapi_key="",
    )
    registry = AdapterRegistry(
        settings,
        adapter_root=tmp_path / "adapters",
        client=client,
    )
    repository, service = _bus(tmp_path / "providers.sqlite3")
    sources = {source.source_id: source for source in initial_sources()}
    parameters = {
        "benzinga_news": {},
        "finnhub_company_news": {},
        "stocktwits_messages": {},
        "tikhub_x_search": {"search_terms": ["MU"]},
        "tikhub_x_user_posts": {"usernames": ["MicronTech"]},
        "newswire_rss": {"rss_urls": ["https://feed.test/rss"]},
    }
    permit_counts: dict[str, int] = {}
    for source_id, source in sources.items():
        binding = service.configure_binding(
            ticker="MU",
            source_id=source_id,
            source_parameters=parameters[source_id],
            actor=UpdateActor.SYSTEM,
        )

        @asynccontextmanager
        async def permit(source_key: str = source_id) -> AsyncIterator[None]:
            permit_counts[source_key] = permit_counts.get(source_key, 0) + 1
            yield

        context = PollContext(
            ticker="MU",
            source=source,
            binding=binding,
            requested_at=NOW,
            request_permit=permit,
        )
        result = await registry.resolve(
            source.adapter_ref, source_version=source.version
        ).poll(context)
        assert len(result.messages) == 1, source_id
        assert result.failures == []
        assert result.messages[0].url.startswith("http")
        assert result.messages[0].published_at.tzinfo is not None
        assert permit_counts[source_id] == 1

    await client.aclose()


def test_agent_tools_mutate_full_control_plane_through_shared_service(
    tmp_path: Path,
) -> None:
    repository, service = _bus(tmp_path / "tools.sqlite3")
    tools = MonitoringToolClient(
        settings=DoxAgentSettings(_env_file=None),
        service=service,
    )

    def call(name: str, value: dict[str, object]) -> dict[str, object]:
        result = tools.for_tool(name).call(
            ToolRequest(
                tool_name=name,
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input=value,
            )
        )
        assert result.status is ResultStatus.SUCCEEDED, result.error
        return result.output

    registered = call(
        "monitoring.register_source",
        {
            "source_id": "micron_ir",
            "display_name": "Micron Investor Relations",
            "kind": "crawler",
            "adapter_ref": "crawler:micron_ir",
            "parameter_schema": {
                "type": "object",
                "properties": {"section_url": {"type": "string", "minLength": 1}},
                "required": ["section_url"],
                "additionalProperties": False,
            },
            "scheduler_group": "ir.micron.com",
            "scheduler_constraints": {
                "minimum_request_gap_seconds": 5,
                "max_concurrency": 1,
            },
        },
    )
    assert registered["source_id"] == "micron_ir"
    call(
        "monitoring.update_source",
        {
            "source_id": "micron_ir",
            "patch": {
                    "adapter_ref": "crawler:micron_ir_v2",
                "scheduler_constraints": {
                    "minimum_request_gap_seconds": 10,
                    "max_concurrency": 2,
                },
            },
            "reason": "crawler revision",
        },
    )
    call(
        "monitoring.update_default_profile",
        {
            "profile_id": "ir-only",
            "entries": [
                {
                    "source_id": "micron_ir",
                    "source_parameters": {"section_url": "https://example.test/ir"},
                    "polling": {"target_interval_seconds": 300},
                    "streaming": {"publication_mode": "immediate"},
                }
            ],
            "reason": "O4 profile",
        },
    )
    service.start_ticker("MU", profile_id="ir-only", actor=UpdateActor.AGENT)
    updated = call(
        "monitoring.update_ticker_config",
        {
            "ticker": "MU",
            "source_id": "micron_ir",
            "source_parameters": {"section_url": "https://example.test/ir/news"},
            "polling": {"enabled": True, "target_interval_seconds": 120},
            "streaming": {
                "publication_mode": "buffered",
                "buffer": {"max_items": 3, "max_wait_seconds": 30},
            },
        },
    )
    binding = updated["binding"]
    assert isinstance(binding, dict)
    assert binding["polling"]["target_interval_seconds"] == 120
    assert binding["streaming"]["publication_mode"] == "buffered"

    deleted = call(
        "monitoring.hard_delete_source",
        {"source_id": "micron_ir", "reason": "retired"},
    )
    assert deleted["source_id"] == "micron_ir"
    assert repository.get_source("micron_ir") is None


class _UsableDocuments:
    def __init__(self) -> None:
        self.bundle = DocumentBundle(
            status=DocumentSetStatus(
                ticker="MU",
                blackboard_run_id="run-mu",
                usable=True,
            )
        )

    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self.bundle

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self.bundle


class _AcceptingRuntimeV2:
    def __init__(self) -> None:
        self.envelopes: list[SourceMessageEnvelope] = []

    def process_pending_effects(self, *, limit: int) -> list[object]:
        return []

    def execute_message(self, value: SourceMessageEnvelope) -> object:
        self.envelopes.append(value)
        return type(
            "AcceptedCase",
            (),
            {
                "case_id": f"case-{len(self.envelopes)}",
                "status": RuntimeCaseStatus.COMPLETED,
                "route": None,
            },
        )()


async def test_scheduler_runtime_v2_handoff_skips_history_then_commits_new_stream(
    tmp_path: Path,
) -> None:
    repository, bus = _bus(tmp_path / "handoff-bus.sqlite3")
    legacy_runtime = PersistentRuntimeExecutionService.from_settings()
    legacy_runtime.repository = InMemoryPersistentRuntimeRepository()
    runtime_v2 = _AcceptingRuntimeV2()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=_UsableDocuments(),  # type: ignore[arg-type]
        monitoring_service=None,
        runtime_service=legacy_runtime,
        runtime_v2_service=runtime_v2,  # type: ignore[arg-type]
        message_bus_v2_service=bus,
        message_bus_v2_enabled=True,
    )
    scheduler.start_ticker("MU", now=NOW, monitor_mode=MonitorMode.MESSAGE_MONITORING)
    source = bus.require_source("benzinga_news")
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    await bus.accept_message(
        source=source,
        binding=binding,
        message=_input("before-trading"),
        bootstrap=False,
    )

    scheduler.set_monitor_mode("MU", "trading", now=NOW + timedelta(seconds=1))
    assert runtime_v2.envelopes == []
    assert repository.get_consumer_offset("persistent_runtime_v2", "MU").stream_offset == 1

    await bus.accept_message(
        source=source,
        binding=binding,
        message=_input("after-trading", published_at=NOW + timedelta(seconds=2)),
        bootstrap=False,
    )
    detail = scheduler.tick_ticker("MU", now=NOW + timedelta(seconds=3))
    assert len(runtime_v2.envelopes) == 1
    assert runtime_v2.envelopes[0].source_message_id.endswith(
        repository.read_stream("MU", after_offset=1)[0].members[0].standard_message_id
    )
    assert repository.get_consumer_offset("persistent_runtime_v2", "MU").stream_offset == 2
    assert detail.event_processing_status.pending_event_count == 0


def test_dashboard_v2_api_uses_native_contract_without_legacy_fields(tmp_path: Path) -> None:
    repository, bus = _bus(tmp_path / "dashboard-bus.sqlite3")
    legacy_runtime = PersistentRuntimeExecutionService.from_settings()
    legacy_runtime.repository = InMemoryPersistentRuntimeRepository()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=_UsableDocuments(),  # type: ignore[arg-type]
        monitoring_service=None,
        runtime_service=legacy_runtime,
        message_bus_v2_service=bus,
        message_bus_v2_enabled=True,
    )
    scheduler.start_ticker("MU", now=NOW)
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )

    config = client.get("/api/dashboard/v1/tickers/MU/message-bus/config")
    assert config.status_code == 200
    benzinga = next(
        source
        for source in config.json()["data"]["sources"]
        if source["source_id"] == "benzinga_news"
    )
    assert benzinga["source_kind"] == "api"
    assert "source_type" not in benzinga
    assert "user_only_fields" not in benzinga

    patched = client.patch(
        "/api/dashboard/v1/tickers/MU/message-bus/config/benzinga_news",
        json={
            "polling": {"target_interval_seconds": 90, "tolerance_ratio": 0.2},
            "streaming": {"publication_mode": "buffered"},
        },
    )
    assert patched.status_code == 200
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    assert binding.polling.target_interval_seconds == 90
    assert binding.streaming.publication_mode is PublicationMode.BUFFERED

    created = client.post(
        "/api/dashboard/v1/message-bus/sources",
        json={
            "source_id": "micron_ir",
            "display_name": "Micron IR",
            "kind": "crawler",
            "adapter_ref": "crawler:micron_ir",
            "parameter_schema": {"type": "object"},
            "scheduler_group": "ir.micron.com",
        },
    )
    assert created.status_code == 200
    assert created.json()["data"]["kind"] == "crawler"

    deleted = client.request(
        "DELETE",
        "/api/dashboard/v1/message-bus/sources/micron_ir",
        json={"reason": "test cleanup"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["historical_messages_preserved"] is True
