from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.schema import (
    CheckStatus,
    CrawlerExecutionRequest,
    CrawlerExecutionStatus,
    CrawlerSourceRegistration,
    CrawlerVersionStatus,
    NetworkMode,
    new_id,
)
from doxagent.dashboard_api import create_app
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.models import AgentName, ResultStatus
from doxagent.persistent_runtime.repository import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler.api import DashboardStateAPI
from doxagent.runtime_scheduler.repository import InMemoryRuntimeSchedulerRepository
from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.crawler_plane import CrawlerPlaneToolClient
from doxagent.tools.schema import ToolRequest


def _settings(tmp_path: Path) -> DoxAgentSettings:
    return DoxAgentSettings(
        message_bus_v2_enabled=True,
        message_bus_v2_sqlite_path=str(tmp_path / "message_bus.sqlite3"),
        message_bus_v2_adapter_root=str(tmp_path / "adapters"),
        message_bus_v2_content_enrichment_enabled=False,
        crawler_plane_root=str(tmp_path / "crawler-plane"),
        crawler_plane_sqlite_path=str(tmp_path / "crawler-plane" / "crawler_plane.sqlite3"),
        crawler_plane_worker_processes=4,
        crawler_plane_execution_timeout_seconds=15,
    )


@pytest.mark.asyncio
async def test_reference_certification_and_move_only_promotion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_crawler_plane_service(settings)
    try:
        working = service.get_version("company_ir_reference", 1)
        assert working.status is CrawlerVersionStatus.WORKING
        assert working.working_path is not None
        assert not (Path(working.working_path) / "manifest.json").exists()

        certification = await service.certify_version("company_ir_reference", 1)
        assert certification.overall is CheckStatus.PASS
        government = await service.certify_version("government_policy_reference", 1)
        assert government.overall is CheckStatus.PASS
        concurrent = await asyncio.gather(
            *[
                service.execute(
                    CrawlerExecutionRequest(
                        crawler_id="company_ir_reference",
                        version=1,
                        ticker="MU",
                        source_id="cert.company_ir_reference",
                        binding_id=f"parallel:{index}",
                        source_parameters={
                            "listing_url": "https://ir.example.test/news",
                            "source_name": "Example IR",
                        },
                        poll_run_id=new_id("parallel_poll"),
                        network_mode=NetworkMode.REPLAY,
                        cassette_ref="tests/replay.json",
                        commit_checkpoint=False,
                    )
                )
                for index in range(8)
            ]
        )
        assert all(item.status is CrawlerExecutionStatus.SUCCEEDED for item in concurrent)

        promoted = service.promote_version("company_ir_reference", 1)
        assert promoted.status is CrawlerVersionStatus.ACTIVE
        assert promoted.working_path is None
        assert promoted.release_path is not None
        assert Path(promoted.release_path).is_dir()
        assert not Path(working.working_path).exists()
        release_entrypoint = Path(promoted.release_path) / "crawler.py"
        assert not release_entrypoint.stat().st_mode & stat.S_IWUSR
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_crawler_poll_lineage_and_checkpoint_ownership(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, message_bus = build_message_bus_v2_service(settings)
    crawler_plane = build_crawler_plane_service(settings, message_bus=message_bus)

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/news":
            body = (
                '<a data-id="LIVE-1" data-published="2026-09-01T12:00:00Z" '
                'href="/news/live-1">Live release</a>'
            )
        else:
            body = "<article>Live release body</article>"
        return httpx.Response(200, text=body, request=request)

    await crawler_plane.http_client.aclose()
    crawler_plane.http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    adapters = AdapterRegistry(
        settings,
        adapter_root=settings.message_bus_v2_adapter_root,
        crawler_plane=crawler_plane,
    )
    scheduler = GlobalPollScheduler(message_bus.repository, message_bus, adapters)
    crawler_execution_id = ""
    try:
        certification = await crawler_plane.certify_version("company_ir_reference", 1)
        assert certification.overall is CheckStatus.PASS
        crawler_plane.promote_version("company_ir_reference", 1)
        source = crawler_plane.register_crawler_source(
            CrawlerSourceRegistration(
                source_id="example_ir",
                display_name="Example IR",
                crawler_id="company_ir_reference",
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "listing_url": {"type": "string"},
                        "source_name": {"type": "string"},
                    },
                    "required": ["listing_url", "source_name"],
                    "additionalProperties": False,
                },
                scheduler_group="example_ir",
            )
        )
        binding = message_bus.configure_binding(
            ticker="MU",
            source_id=source.source_id,
            source_parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
            actor=UpdateActor.AGENT,
        )

        execution = await scheduler._poll(source, binding, utc_now())

        assert execution.error_code is None
        assert execution.crawler_execution_id is not None
        crawler_execution_id = execution.crawler_execution_id
        assert execution.poll_run_id.startswith("poll_")
        assert message_bus.repository.get_poll_state(binding).checkpoint == {}
        crawler_result = crawler_plane.get_execution(execution.crawler_execution_id)
        assert crawler_result.checkpoint_after == {"seen_ids": ["LIVE-1"]}
        assert crawler_result.message_bus_telemetry["poll_run_id"] == execution.poll_run_id
        raw = message_bus.repository.list_raw(ticker="MU")
        assert raw[0].metadata["crawler_execution_id"] == execution.crawler_execution_id
    finally:
        await adapters.close()
        await crawler_plane.close()
        message_bus.repository.close()

    _, reopened_bus = build_message_bus_v2_service(settings)
    reopened = build_crawler_plane_service(settings, message_bus=reopened_bus)
    try:
        assert reopened.get_crawler("company_ir_reference").active_version == 1
        assert (
            reopened.get_execution(crawler_execution_id).status is CrawlerExecutionStatus.SUCCEEDED
        )
        checkpoint = reopened.repository.get_checkpoint("company_ir_reference", "MU:example_ir")
        assert checkpoint is not None
        assert checkpoint.value == {"seen_ids": ["LIVE-1"]}
    finally:
        await reopened.close()
        reopened_bus.repository.close()


@pytest.mark.asyncio
async def test_failure_persists_replay_cassette_and_failure_bundle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_crawler_plane_service(settings)

    async def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked", request=request)

    await service.http_client.aclose()
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    try:
        result = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert result.status is CrawlerExecutionStatus.FAILED
        assert result.cassette_ref is not None
        cassette = service.repository.get_cassette(result.cassette_ref)
        assert cassette is not None
        assert cassette.exchanges[0].status_code == 403
        assert cassette.exchanges[0].response_body == "blocked"
        artifacts = service.get_execution_artifacts(result.execution_id)
        assert any(item.kind == "failure_bundle" for item in artifacts)
        regression = service.add_failure_to_regression(result.execution_id)
        assert regression.parameters["listing_url"] == "https://ir.example.test/news"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_o4_tools_use_the_crawler_plane_application_service(tmp_path: Path) -> None:
    service = build_crawler_plane_service(_settings(tmp_path))
    tools = CrawlerPlaneToolClient(service=service)
    try:
        result = tools.for_tool("crawler_plane.get").call(
            ToolRequest(
                tool_name="crawler_plane.get",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference"},
            )
        )
        assert result.status is ResultStatus.SUCCEEDED
        assert result.output["crawler"]["crawler_id"] == "company_ir_reference"
        working_path = result.output["versions"][0]["working_path"]
        assert Path(working_path).parts[-2:] == ("company_ir_reference", "v1")
        certified = tools.for_tool("crawler_plane.certify").call(
            ToolRequest(
                tool_name="crawler_plane.certify",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference", "version": 1},
            )
        )
        assert certified.status is ResultStatus.SUCCEEDED
        assert certified.output["overall"] == "PASS"
        promoted = tools.for_tool("crawler_plane.promote").call(
            ToolRequest(
                tool_name="crawler_plane.promote",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference", "version": 1},
            )
        )
        assert promoted.status is ResultStatus.SUCCEEDED
        assert promoted.output["status"] == "ACTIVE"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_human_api_uses_the_same_crawler_plane_service(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, message_bus = build_message_bus_v2_service(settings)
    crawler_plane = build_crawler_plane_service(settings, message_bus=message_bus)
    legacy_runtime = PersistentRuntimeExecutionService.from_settings(settings)
    legacy_runtime.repository = InMemoryPersistentRuntimeRepository()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=cast(Any, object()),
        monitoring_service=None,
        runtime_service=legacy_runtime,
        message_bus_v2_service=message_bus,
        crawler_plane_service=crawler_plane,
        message_bus_v2_enabled=True,
    )
    try:
        client = TestClient(
            create_app(
                mode="real",
                auth_mode="mock-open",
                dashboard_api=DashboardStateAPI(scheduler),
            )
        )
        listed = client.get("/api/dashboard/v1/crawler-plane/crawlers")
        assert listed.status_code == 200
        assert len(listed.json()["data"]["crawlers"]) == 2
        fetched = client.get("/api/dashboard/v1/crawler-plane/crawlers/company_ir_reference")
        assert fetched.status_code == 200
        fetched_path = Path(fetched.json()["data"]["versions"][0]["working_path"])
        assert fetched_path.parts[-2:] == ("company_ir_reference", "v1")
    finally:
        await crawler_plane.close()
        message_bus.repository.close()
